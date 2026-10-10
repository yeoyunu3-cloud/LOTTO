#!/bin/zsh
# GitHub Actions가 수집한 최신 데이터를 맥으로 받아오고, 새 회차가 있으면 알림
cd "$(dirname "$0")"
before=$(git rev-parse HEAD)
git pull -q --ff-only || exit 1
if [[ "$before" != "$(git rev-parse HEAD)" ]]; then
  IFS=, read round day n1 n2 n3 n4 n5 n6 bonus _ < <(tail -1 lotto_all.csv)
  echo "[$(date '+%F %T')] ${round}회 받아옴: $n1 $n2 $n3 $n4 $n5 $n6 + $bonus"
  osascript -e "display notification \"$n1, $n2, $n3, $n4, $n5, $n6 + 보너스 $bonus\" with title \"로또 ${round}회 당첨번호\""
else
  echo "[$(date '+%F %T')] 새 데이터 없음"
fi
