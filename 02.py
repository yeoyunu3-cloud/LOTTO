"""
lotto_all.csv 통계를 바탕으로 로또 번호 추천 (보너스 포함)

1. 가중치: 전체 출현 빈도 50% + 최근 100회 출현 빈도 50% → 자주/요즘 나온 번호일수록 잘 뽑힘
2. 필터: 역대 당첨번호 대부분이 속하는 패턴만 통과
   - 6개 합계 98~177 (역대 당첨의 약 80%가 이 구간)
   - 홀수 2~4개, 저번호(1~22) 2~4개
   - 3개 이상 연속번호(예: 7,8,9) 제외
   - 역대 1등 번호와 완전히 같은 조합 제외
3. 보너스: 보너스 출현 빈도 가중치로 본번호 6개를 제외하고 1개

※ 로또는 매 회 독립 추첨이라 어떤 조합이든 1등 확률은 1/8,145,060로 같다. 재미로만!

실행: python3 02.py        # 5게임
      python3 02.py 10     # 10게임
"""

import csv
import random
import sys
from collections import Counter
from pathlib import Path

DATA_CSV = Path(__file__).resolve().parent / "lotto_all.csv"
NUM_COLS = ["번호1", "번호2", "번호3", "번호4", "번호5", "번호6"]


def load():
    with open(DATA_CSV, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    draws = [tuple(int(r[c]) for c in NUM_COLS) for r in rows]
    bonuses = [int(r["보너스"]) for r in rows]
    return draws, bonuses


def make_weights(draws, bonuses):
    total = Counter(n for d in draws for n in d)
    recent = Counter(n for d in draws[-100:] for n in d)
    t_sum, r_sum = sum(total.values()), sum(recent.values())
    main_w = {n: 0.5 * total[n] / t_sum + 0.5 * recent[n] / r_sum for n in range(1, 46)}
    bonus_cnt = Counter(bonuses)
    bonus_w = {n: bonus_cnt[n] + 1 for n in range(1, 46)}  # +1: 한 번도 안 나온 번호도 기회는 줌
    return main_w, bonus_w


def weighted_sample(weights, k):
    """가중치 기반 비복원 추출."""
    pool = dict(weights)
    picked = []
    for _ in range(k):
        n = random.choices(list(pool), weights=list(pool.values()))[0]
        picked.append(n)
        del pool[n]
    return sorted(picked)


def looks_like_winner(nums, past):
    odd = sum(n % 2 for n in nums)
    low = sum(n <= 22 for n in nums)
    run3 = any(nums[i] + 2 == nums[i + 1] + 1 == nums[i + 2] for i in range(4))
    return (98 <= sum(nums) <= 177 and 2 <= odd <= 4 and 2 <= low <= 4
            and not run3 and tuple(nums) not in past)


def recommend(games=5):
    draws, bonuses = load()
    main_w, bonus_w = make_weights(draws, bonuses)
    past = set(draws)
    results = []
    while len(results) < games:
        nums = weighted_sample(main_w, 6)
        if not looks_like_winner(nums, past) or nums in [r[0] for r in results]:
            continue
        bonus = weighted_sample({n: w for n, w in bonus_w.items() if n not in nums}, 1)[0]
        results.append((nums, bonus))
    return len(draws), results


def main():
    games = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    latest, results = recommend(games)
    print(f"=== {latest + 1}회 추천 번호 (1~{latest}회 데이터 기반) ===")
    for i, (nums, bonus) in enumerate(results):
        odd = sum(n % 2 for n in nums)
        print(f"{chr(65 + i)}게임: {' '.join(f'{n:2}' for n in nums)}  + 보너스 {bonus:2}"
              f"   (합 {sum(nums)}, 홀{odd}:짝{6 - odd})")
    print("\n※ 어떤 조합이든 1등 확률은 1/8,145,060로 같아요. 재미로만!")


if __name__ == "__main__":
    main()
