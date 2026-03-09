# AI Daily Briefing

RSS / 시장데이터 / LLM을 결합해서 일중 시황 초안을 만들고, GitHub Actions로 정해진 시각에 텔레그램으로 자동 전송하는 프로젝트입니다.

## 동작 개요

1. RSS 수집
2. 정규화 / 중복제거 / 시맨틱 클러스터링
3. 시장 데이터(yfinance, pykrx) 결합
4. Fact Pack 생성
5. LLM 기반 보고서 생성
6. Markdown / Story Markdown 저장
7. 텔레그램 전송

## 로컬 실행

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
