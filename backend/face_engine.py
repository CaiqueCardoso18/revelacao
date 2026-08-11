"""Local face detection + embedding, no network calls at inference time.

Model weights are downloaded once by insightface into ~/.insightface on first
run (needs internet that first time only); every scan after that is fully
local and offline.
"""

import cv2
import numpy as np

_analyzer = None


def get_analyzer():
    global _analyzer
    if _analyzer is None:
        from insightface.app import FaceAnalysis

        _analyzer = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        _analyzer.prepare(ctx_id=0, det_size=(640, 640))
    return _analyzer


def detect_faces(image_path):
    """Returns a list of dicts: {bbox: (x, y, w, h), det_score: float, embedding: np.ndarray[512]}."""
    data = np.fromfile(str(image_path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        return []

    analyzer = get_analyzer()
    faces = analyzer.get(img)

    results = []
    for face in faces:
        x1, y1, x2, y2 = face.bbox
        embedding = face.normed_embedding
        if embedding is None:
            continue
        results.append(
            {
                "bbox": (float(x1), float(y1), float(x2 - x1), float(y2 - y1)),
                "det_score": float(face.det_score),
                "embedding": embedding.astype(np.float32),
            }
        )
    return results
