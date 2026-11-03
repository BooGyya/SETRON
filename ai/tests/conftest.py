import os, sys
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
H = os.environ.get("SVHM_DATA", _DATA)      # 합성 데이터 v2 (tests/data)
U = os.environ.get("SVHM_UPLOADS", _DATA)   # 멘토 교육용 원본 (같은 폴더)

FAMILY_FILES = {
    1: (f"{H}/P_PJT_M-C3_SW_정기_Release_List_교육용.xlsx", "구UI"),
    2: (f"{H}/P_PJT_M-C3_SW_정기_Release_List_교육용.xlsx", "통합UI"),
    4: (f"{H}/P_PJT_M-C3_Foundry_SW_Release_List_교육용.xlsx", "통합UI"),
    5: (f"{U}/P_PJT_M-C4_SW_정기_Release_List_교육용.xlsx", None),
    6: (f"{H}/P_PJT_M-C4_Foundry_SW_Release_List_교육용.xlsx", None),
    7: (f"{H}/P_PJT_M-O2_SW_정기_Release_List_교육용.xlsx", "Memory"),
    8: (f"{H}/P_PJT_M-O2_Foundry_SW_Release_List_교육용.xlsx", "Foundry"),
    9: (f"{H}/P_PJT_M-O3_SW_정기_Release_List_교육용.xlsx", None),
    10: (f"{U}/P_PJT_M-O5_SW_정기_Release_List_교육용.xlsx", None),
    11: (f"{H}/P_PJT_KCTC_SW_정기_Release_List_교육용.xlsx", "Inline Ox"),
    12: (f"{H}/P_PJT_KCTC_SW_정기_Release_List_교육용.xlsx", "Inline O2"),
    13: (f"{H}/P_PJT_KCTC_SW_정기_Release_List_교육용.xlsx", "Cluster Ox"),
    14: (f"{H}/P_PJT_KCTC_SW_정기_Release_List_교육용.xlsx", "Cluster O2"),
}

@pytest.fixture(scope="session")
def registry():
    from svhm.core.pipeline import build_registry
    return build_registry(FAMILY_FILES)
