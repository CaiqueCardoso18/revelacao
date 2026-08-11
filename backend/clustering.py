"""Groups detected faces into unique people using cosine distance + agglomerative clustering.

A face that doesn't join any cluster of at least `min_cluster_size` members
stays unassigned (person_id = NULL) -- that is the "não identificados" bucket
in the UI. Clusters that sit just outside the merge threshold of another
cluster are flagged as merge suggestions for a human to confirm, since a
person fragmenting into two clusters under different lighting/angles is the
main way this kind of pipeline splits someone in two.

Deliberately NOT DBSCAN: DBSCAN links two faces if a *chain* of intermediate
faces connects them within `eps`, even when the two ends of that chain look
nothing alike. On a real rehearsal-photo set that chaining reliably merges
dozens of unrelated people into one giant, internally-random "person" --
verified on a real 8,201-face event where DBSCAN's biggest cluster had a
median internal similarity of ~0.05 (i.e. unrelated faces). Average-linkage
agglomerative clustering only merges two groups when their *average*
pairwise similarity clears the bar, so it can't be bridged by one ambiguous
face the way DBSCAN can -- confirmed on that same dataset to produce
internally coherent clusters (median internal similarity ~0.7) instead.

This runs more than once per event -- periodically during a long scan (so
people show up live instead of only after the last photo), and again
whenever someone re-scans a folder for new photos. Each run recomputes every
cluster from scratch, so `_match_clusters_to_old_people` is what keeps a
person's name (default or renamed) attached to "the same person" across runs,
instead of everyone getting shuffled and renumbered every pass.
"""

from collections import Counter

import numpy as np
from scipy.optimize import linear_sum_assignment
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics.pairwise import cosine_similarity

from . import db

REVIEW_MARGIN = 0.15
REUSE_SIM_THRESHOLD = 0.55


def cluster_event(event_id):
    conn = db.get_conn()
    try:
        event = db.get_event(conn, event_id)
        faces = db.list_faces(conn, event_id)
        old_identities = _load_old_identities(conn, event_id, faces)

        if not faces:
            db.clear_person_assignments(conn, event_id)
            return

        face_ids = [f["id"] for f in faces]
        embeddings = np.stack([db.blob_to_embedding(f["embedding"]) for f in faces])

        eps = event["cluster_eps"]
        min_cluster_size = event["min_cluster_size"]

        labels = AgglomerativeClustering(
            n_clusters=None, distance_threshold=eps, metric="cosine", linkage="average"
        ).fit_predict(embeddings)

        # AgglomerativeClustering has no min_samples concept like DBSCAN --
        # every face gets a label. Undersized clusters become noise (-1) here.
        sizes = Counter(labels)
        labels = np.array([lbl if sizes[lbl] >= min_cluster_size else -1 for lbl in labels])

        cluster_ids = sorted(set(labels) - {-1})
        clusters = []
        for cluster_id in cluster_ids:
            member_idx = np.where(labels == cluster_id)[0]
            centroid = embeddings[member_idx].mean(axis=0)
            centroid = centroid / np.linalg.norm(centroid)
            clusters.append(
                {
                    "member_idx": member_idx,
                    "centroid": centroid,
                    "size": len(member_idx),
                }
            )
        clusters.sort(key=lambda c: c["size"], reverse=True)

        reused_names = _match_clusters_to_old_people(clusters, old_identities)
        merge_target = _find_merge_suggestions(clusters, eps)

        db.clear_person_assignments(conn, event_id)

        person_ids = [None] * len(clusters)
        for i, cluster in enumerate(clusters):
            name = reused_names[i] or f"Pessoa {db.next_person_number(conn, event_id):02d}"
            cover_face_id = face_ids[cluster["member_idx"][0]]
            person_ids[i] = db.create_person(
                conn, event_id, name, cover_face_id=cover_face_id
            )

        for i, cluster in enumerate(clusters):
            for member in cluster["member_idx"]:
                db.assign_face_person(conn, face_ids[member], person_ids[i])

        for smaller_idx, larger_idx in merge_target.items():
            conn.execute(
                "UPDATE people SET merge_suggestion_for = ? WHERE id = ?",
                (person_ids[larger_idx], person_ids[smaller_idx]),
            )

        conn.commit()
    finally:
        conn.close()


def _load_old_identities(conn, event_id, faces):
    """Centroid + name for each person as they stood before this pass wipes them."""
    people = db.list_people(conn, event_id)
    if not people:
        return []

    vectors_by_person = {}
    for f in faces:
        if f["person_id"] is not None:
            vectors_by_person.setdefault(f["person_id"], []).append(
                db.blob_to_embedding(f["embedding"])
            )

    identities = []
    for p in people:
        vectors = vectors_by_person.get(p["id"])
        if not vectors:
            continue
        centroid = np.mean(vectors, axis=0)
        centroid = centroid / np.linalg.norm(centroid)
        identities.append({"name": p["name"], "centroid": centroid})
    return identities


def _match_clusters_to_old_people(clusters, old_identities):
    """Returns a list the same length as `clusters`: the carried-over name, or None for a fresh person.

    Matches greedily-optimally by centroid similarity (Hungarian algorithm) so
    the same real person keeps the same name/number run after run, instead of
    getting renamed back to a generic "Pessoa NN" every time this re-runs.
    Only reuses a name when the match is confident (>= REUSE_SIM_THRESHOLD) --
    a low-confidence match would risk handing one person's custom name to
    someone else entirely.
    """
    result = [None] * len(clusters)
    if not clusters or not old_identities:
        return result

    new_centroids = np.stack([c["centroid"] for c in clusters])
    old_centroids = np.stack([o["centroid"] for o in old_identities])
    sim = cosine_similarity(new_centroids, old_centroids)

    row_idx, col_idx = linear_sum_assignment(-sim)
    for r, c in zip(row_idx, col_idx):
        if sim[r, c] >= REUSE_SIM_THRESHOLD:
            result[r] = old_identities[c]["name"]
    return result


def _find_merge_suggestions(clusters, eps):
    """Returns {smaller_cluster_index: larger_cluster_index} for pairs worth a human look.

    DBSCAN only merges clusters that are connected by a chain of points each
    within `eps` of each other -- two clusters can have very similar centroids
    and still end up separate (e.g. one outlier point breaks the chain, or the
    two blobs are tight but offset). So this checks centroid similarity
    directly rather than reusing eps as an upper bound: anything at or above
    `review_sim` is worth flagging, regardless of how it compares to eps.
    """
    review_sim = (1 - eps) - REVIEW_MARGIN

    suggestions = {}
    if len(clusters) < 2:
        return suggestions

    centroids = np.stack([c["centroid"] for c in clusters])
    sim_matrix = cosine_similarity(centroids)

    best_match = {}
    for i in range(len(clusters)):
        for j in range(len(clusters)):
            if i == j:
                continue
            sim = sim_matrix[i, j]
            if sim >= review_sim and clusters[j]["size"] > clusters[i]["size"]:
                if i not in best_match or sim > best_match[i][1]:
                    best_match[i] = (j, sim)

    for smaller_idx, (larger_idx, _sim) in best_match.items():
        suggestions[smaller_idx] = larger_idx
    return suggestions
