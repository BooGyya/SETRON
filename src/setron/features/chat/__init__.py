"""AI 분석 대화 (멘토 5차 이후 VER4 와이어프레임 10-1 ~ 10-5).

질문 하나를 '요청 종류(무엇을 원하나)'와 '조건(어떤 범위로)'으로 나누고(recognize), 요청 종류마다 이미 있는
기능(집계·우선순위·예측·원인 설명·조회 결과 내보내기)을 불러 답 묶음 하나를 만든다(answer).
숫자는 모두 DB 집계에서 나오고, AI는 질문 이해와 원인 설명 문장만 맡는다. 대시보드 조건은 바꾸지 않는다.
"""

from .answers import ChatData, answer
from .polish import polish
from .recognize import recognize
from .schemas import ChatAnswer, ChatRequest, Intent

__all__ = ["ChatAnswer", "ChatData", "ChatRequest", "Intent", "answer", "polish", "recognize"]
