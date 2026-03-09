# Daily 시황 Bot 업그레이드 패치 가이드

이번 패치는 아래 4가지를 한 번에 반영합니다.

1. 기사 단위 → 이벤트 단위 구조화
2. 한국증시 relevance / market-moving 우선순위 반영
3. 수급·업종·시장맥락을 fact pack에 구조화
4. LLM 프롬프트를 '뉴스요약'이 아니라 '애널리스트형 시황'으로 변경

## 교체해야 하는 파일

- `src/run_daily.py`
- `src/fact_pack.py`
- `src/nlp/tagger.py`
- `src/nlp/filtering.py`
- `src/llm/prompts.py`
- `src/llm/writer.py`
- `config.yaml`

## 새로 추가되는 파일

- `src/nlp/event_enricher.py`
- `src/market/market_context.py`

## 적용 방법

기존 repository 파일을 이 패치본 파일로 그대로 덮어쓰면 됩니다.

## 기대 효과

- 단순 headline 나열 감소
- market-moving 이벤트 우선 선별
- 한국시장 기준 해석 강화
- 수급/업종/금리/환율 연결 강화
- top_drivers 품질 개선
