"""
로또 6/45 전 회차 당첨번호 수집 + 전처리 + 번호별 출현 빈도 분석

- 데이터 출처: 동행복권 (https://www.dhlottery.co.kr/lt645/result)
  사이트 개편 후 예전 API(common.do?method=getLottoNumber)는 막혔고,
  지금은 /lt645/selectPstLt645InfoNew.do 가 한 번에 10회차씩 JSON으로 내려준다.
- 표준 라이브러리만 사용 (pip install 불필요)
- 한 번 받은 회차는 lotto_all.csv에 저장해두고, 다음 실행부터는 새 회차만 추가로 받는다.

실행: python3 01.py           # 한 번 수집
      python3 01.py --watch   # 이번 주 결과가 올라올 때까지 5분마다 재확인 (자동 실행용)
자동 수집: GitHub Actions(.github/workflows/lotto.yml)가 매주 토요일 밤에 실행 → CSV 커밋
맥 동기화: launchd(com.lotto.update.plist)가 lotto_sync.sh로 git pull + 새 회차 알림
"""

import csv
import json
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_CSV = BASE_DIR / "lotto_all.csv"
FREQ_CSV = BASE_DIR / "lotto_frequency.csv"

API_URL = "https://www.dhlottery.co.kr/lt645/selectPstLt645InfoNew.do"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json",
    "Referer": "https://www.dhlottery.co.kr/lt645/result",
}
FIRST_DRAW = date(2002, 12, 7)  # 1회차 추첨일
REQUEST_DELAY = 0.3             # 서버 부담 줄이기용 요청 간격(초)
KST = ZoneInfo("Asia/Seoul")
FIRST_DRAW_AT = datetime(2002, 12, 7, 20, 35, tzinfo=KST)  # 추첨: 매주 토요일 20:35
WATCH_INTERVAL = 5 * 60         # --watch: 재확인 간격(초)
WATCH_TIMEOUT = 3 * 60 * 60     # --watch: 최대 대기 시간(초)

COLUMNS = [
    "회차", "추첨일", "번호1", "번호2", "번호3", "번호4", "번호5", "번호6", "보너스",
    "1등_당첨자수", "1등_1인당금액", "2등_당첨자수", "2등_1인당금액",
    "3등_당첨자수", "3등_1인당금액", "4등_당첨자수", "4등_1인당금액",
    "5등_당첨자수", "5등_1인당금액", "총판매금액",
]
NUM_COLS = ["번호1", "번호2", "번호3", "번호4", "번호5", "번호6"]


# ---------------------------------------------------------------- 크롤링

def make_ssl_context():
    """python.org에서 받은 macOS용 파이썬은 루트 인증서가 없어서 SSL 검증이 실패한다.
    (Install Certificates.command를 안 돌린 경우) → macOS 키체인의 인증서를 대신 사용."""
    ctx = ssl.create_default_context()
    if sys.platform == "darwin" and not ctx.get_ca_certs():
        keychains = ["/System/Library/Keychains/SystemRootCertificates.keychain",
                     "/Library/Keychains/System.keychain"]
        pem = subprocess.run(["security", "find-certificate", "-a", "-p", *keychains],
                             capture_output=True, text=True).stdout
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
            f.write(pem)
        ctx = ssl.create_default_context(cafile=f.name)
        Path(f.name).unlink()
    return ctx


SSL_CTX = make_ssl_context()


def fetch(params, retries=3):
    """API 호출 → 회차 리스트 반환. 실패하면 잠깐 쉬고 재시도."""
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as res:
                body = json.loads(res.read().decode("utf-8"))
            return (body.get("data") or {}).get("list") or []
        except Exception as e:
            if attempt == retries:
                raise RuntimeError(f"요청 실패: {url} ({e})") from e
            time.sleep(attempt * 2)


def find_latest_round():
    """오늘 날짜로 회차를 추정한 뒤, 실제 API 응답으로 최신 회차를 확정."""
    guess = (date.today() - FIRST_DRAW).days // 7 + 1
    # center 조회는 기준 회차 앞뒤로 최대 10개를 주므로, 추정치 근처를 조회해서 최댓값을 취함
    while True:
        rows = fetch({"srchDir": "center", "srchLtEpsd": guess})
        if not rows:            # 추정치가 아직 추첨 전이면 한 회차씩 내려가며 재시도
            guess -= 1
            continue
        latest = max(r["ltEpsd"] for r in rows)
        if latest < guess + 4:  # 더 최신 회차가 없음
            return latest
        guess = latest          # 응답이 꽉 찼으면 더 최신이 있을 수 있음


def crawl(start_round, end_round):
    """start_round ~ end_round 회차를 10개 단위로 받아옴 (최신 → 과거 방향)."""
    raw = {}
    # older 조회는 실제로 존재하는 회차를 커서로 줘야 하므로, 첫 페이지는 center로 받음
    params = {"srchDir": "center", "srchLtEpsd": end_round}
    while True:
        rows = fetch(params)
        if not rows:
            break
        for r in rows:
            if start_round <= r["ltEpsd"] <= end_round:
                raw[r["ltEpsd"]] = r
        cursor = min(r["ltEpsd"] for r in rows)
        print(f"\r  수집 중... {len(raw)}/{end_round - start_round + 1}회차", end="", flush=True)
        if cursor <= start_round:
            break
        params = {"srchDir": "older", "srchCursorLtEpsd": cursor}
        time.sleep(REQUEST_DELAY)
    print()
    return raw


# ---------------------------------------------------------------- 전처리

def preprocess(r):
    """API 원본 한 건 → 정리된 dict. 번호는 오름차순 정렬, 날짜는 YYYY-MM-DD."""
    nums = sorted(int(r[f"tm{i}WnNo"]) for i in range(1, 7))
    ymd = str(r["ltRflYmd"])
    row = {
        "회차": int(r["ltEpsd"]),
        "추첨일": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}",
        **dict(zip(NUM_COLS, nums)),
        "보너스": int(r["bnsWnNo"]),
        "총판매금액": int(r["rlvtEpsdSumNtslAmt"] or 0),
    }
    for k in range(1, 6):
        row[f"{k}등_당첨자수"] = int(r[f"rnk{k}WnNope"] or 0)
        row[f"{k}등_1인당금액"] = int(r[f"rnk{k}WnAmt"] or 0)
    return row


def validate(rows):
    """번호 범위/중복, 회차 누락 등 데이터 이상 여부 검사."""
    problems = []
    for row in rows:
        nums = [row[c] for c in NUM_COLS]
        if len(set(nums)) != 6 or not all(1 <= n <= 45 for n in nums):
            problems.append(f"{row['회차']}회: 당첨번호 이상 {nums}")
        if not 1 <= row["보너스"] <= 45 or row["보너스"] in nums:
            problems.append(f"{row['회차']}회: 보너스번호 이상 {row['보너스']}")
    rounds = [row["회차"] for row in rows]
    missing = sorted(set(range(1, max(rounds) + 1)) - set(rounds))
    if missing:
        problems.append(f"누락된 회차: {missing}")
    return problems


# ---------------------------------------------------------------- 저장/로드

def load_csv():
    if not DATA_CSV.exists():
        return {}
    with open(DATA_CSV, encoding="utf-8-sig", newline="") as f:
        return {int(r["회차"]): {k: (v if k == "추첨일" else int(v)) for k, v in r.items()}
                for r in csv.DictReader(f)}


def save_csv(path, columns, rows):
    # utf-8-sig: 엑셀에서 열어도 한글이 안 깨짐
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------- 분석

def frequency(rows):
    main = Counter(n for row in rows for n in (row[c] for c in NUM_COLS))
    bonus = Counter(row["보너스"] for row in rows)
    recent = Counter(n for row in rows[-100:] for n in (row[c] for c in NUM_COLS))
    last_seen = {}
    for row in rows:
        for c in NUM_COLS:
            last_seen[row[c]] = row["회차"]
    latest = rows[-1]["회차"]

    result = []
    for n in range(1, 46):
        result.append({
            "번호": n,
            "출현횟수": main[n],
            "출현률(%)": round(main[n] / len(rows) * 100, 2),
            "보너스출현": bonus[n],
            "보너스포함합계": main[n] + bonus[n],
            "최근100회_출현": recent[n],
            "마지막출현회차": last_seen.get(n, 0),
            "미출현_회차수": latest - last_seen.get(n, 0),
        })
    return sorted(result, key=lambda x: (-x["출현횟수"], x["번호"]))


def expected_round():
    """지금 시각(KST) 기준으로 이미 추첨이 끝났어야 하는 마지막 회차."""
    return (datetime.now(KST) - FIRST_DRAW_AT) // timedelta(weeks=1) + 1


def notify(title, message):
    """macOS 알림 센터에 알림 띄우기."""
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e",
                        f"display notification {json.dumps(message, ensure_ascii=False)} with title {json.dumps(title, ensure_ascii=False)}"])


def wait_for_new_round():
    """이번 회차 결과가 API에 올라올 때까지 주기적으로 재확인."""
    target = expected_round()
    deadline = time.time() + WATCH_TIMEOUT
    while True:
        latest = find_latest_round()
        if latest >= target:
            return latest
        if time.time() > deadline:
            print(f"{target}회 결과가 아직 안 올라옴 → 다음 실행 때 다시 시도")
            return latest
        print(f"[{datetime.now(KST):%H:%M}] {target}회 결과 대기 중 (현재 {latest}회까지 공개)")
        time.sleep(WATCH_INTERVAL)


def main():
    watch = "--watch" in sys.argv
    data = load_csv()
    print(f"[{datetime.now(KST):%Y-%m-%d %H:%M}] 최신 회차 확인 중...")
    latest = wait_for_new_round() if watch else find_latest_round()
    have = max(data) if data else 0
    print(f"최신 회차: {latest}회 / 저장된 데이터: {have}회까지")

    if have < latest:
        raw = crawl(have + 1, latest)
        data.update({k: preprocess(v) for k, v in raw.items()})

    rows = [data[k] for k in sorted(data)]
    problems = validate(rows)
    if problems:
        print("⚠️  데이터 점검 결과:")
        for p in problems:
            print("  -", p)
    else:
        print(f"데이터 점검 OK (1~{rows[-1]['회차']}회, 누락/이상치 없음)")

    save_csv(DATA_CSV, COLUMNS, rows)
    freq = frequency(rows)
    save_csv(FREQ_CSV, list(freq[0].keys()), freq)

    print(f"\n저장: {DATA_CSV.name} ({len(rows)}행), {FREQ_CSV.name}")
    print(f"\n=== 가장 많이 나온 번호 TOP 10 (1~{rows[-1]['회차']}회, 보너스 제외) ===")
    for i, f in enumerate(freq[:10], 1):
        print(f"{i:2}. {f['번호']:2}번  {f['출현횟수']}회 ({f['출현률(%)']}%)  최근100회 {f['최근100회_출현']}회")
    print("\n=== 가장 적게 나온 번호 5개 ===")
    for f in freq[-5:][::-1]:
        print(f"    {f['번호']:2}번  {f['출현횟수']}회")
    print("\n=== 오래 안 나온 번호 5개 ===")
    for f in sorted(freq, key=lambda x: -x["미출현_회차수"])[:5]:
        print(f"    {f['번호']:2}번  {f['미출현_회차수']}회째 미출현 (마지막 {f['마지막출현회차']}회)")
    last = rows[-1]
    nums = ", ".join(str(last[c]) for c in NUM_COLS)
    print(f"\n최신 {last['회차']}회 ({last['추첨일']}): [{nums}] + 보너스 {last['보너스']}")
    if watch and latest > have:
        notify(f"로또 {last['회차']}회 당첨번호", f"{nums} + 보너스 {last['보너스']}")


if __name__ == "__main__":
    main()
