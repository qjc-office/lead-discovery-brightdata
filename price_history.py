#!/usr/bin/env python3
"""여러 날짜의 amazon_products 스냅샷을 모아 가격 이력 표로 보여준다.

같은 ASIN이 여러 날짜 스냅샷에 모두 존재할 때만 표시한다.
discover_new 방식이라 매일 검색 상위 25개가 달라지므로,
'계속 추적된 상품'만 골라내는 것이 이 스크립트의 핵심이다.

fetch_postings.py를 이틀 이상 실행해 data/amazon_products_YYYYMMDD.csv가
2개 이상 쌓인 뒤에 돌린다. asin/final_price 컬럼을 쓰므로 amazon_products
계열 대상 전용이다.

사용: python3 price_history.py [--target amazon_products]
"""
import csv, glob, os, re, sys, argparse

def load(path):
    with open(path, newline='', encoding='utf-8') as f:
        return {r['asin']: r for r in csv.DictReader(f) if r.get('asin')}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--target', default='amazon_products')
    a = ap.parse_args()

    files = sorted(glob.glob(f'data/{a.target}_*.csv'))
    if len(files) < 2:
        print('스냅샷이 2개 미만입니다.'); return 1

    dates, snaps = [], []
    for p in files:
        m = re.search(r'_(\d{8})\.csv$', p)
        if not m:
            continue
        d = m.group(1)
        dates.append(f'{d[4:6]}/{d[6:8]}')
        snaps.append(load(p))

    tracked = set(snaps[0])
    for s in snaps[1:]:
        tracked &= set(s)

    print(f'추적 기간: {dates[0]} ~ {dates[-1]}  ({len(dates)}개 스냅샷)')
    print(f'전 기간 계속 잡힌 상품: {len(tracked)}개\n')

    header = f'{"상품":<44}' + ''.join(f'{d:>10}' for d in dates) + '   변동'
    print(header)
    print('-' * len(header))

    movers = []
    for asin in sorted(tracked):
        prices = [s[asin].get('final_price', '') for s in snaps]
        title = (snaps[-1][asin].get('title') or '')[:42]
        try:
            nums = [float(p) for p in prices]
        except ValueError:
            continue
        delta = nums[-1] - nums[0]
        mark = '' if abs(delta) < 0.01 else f'{delta:+.2f} ({delta / nums[0] * 100:+.1f}%)'
        row = f'{title:<44}' + ''.join(f'{p:>10}' for p in prices) + f'   {mark}'
        print(row)
        if mark:
            movers.append((title, prices, mark))

    print()
    if movers:
        print(f'가격이 움직인 상품: {len(movers)}개')
        for t, p, m in movers:
            print(f'  {t}')
            print(f'    {" -> ".join(p)}   {m}')
    else:
        print('가격 변동 없음')
    return 0

if __name__ == '__main__':
    sys.exit(main())
