import json

with open('test_fuyao_data.json', 'r', encoding='utf-8') as f:
    fuyao = json.load(f)

print('Indicators Status:', fuyao['indicators_status'])
print('Indicators RateLimit:', {k: v for k, v in fuyao['indicators_headers'].items() if 'ratelimit' in k.lower()})

ind_data = fuyao['indicators_data']
if ind_data.get('data'):
    print('Thscode:', ind_data['data'].get('thscode'))
    print('Report:', ind_data['data'].get('report'))
    for ab in ind_data['data'].get('abilities', []):
        print('Ability:', ab.get('type'))
        for item in ab.get('indicators', []):
            print(f"   {item.get('index_id')}: {item.get('value')} ({item.get('name')})")

print('\nIncome Statement Sample:')
inc_data = fuyao['income_statement_data']
if inc_data.get('data') and inc_data['data'].get('item'):
    item0 = inc_data['data']['item'][0]
    print('Keys:', list(item0.keys()))
    for k in ['thscode', 'period', 'fiscal_year', 'fiscal_period', 'report_date_ms', 'period_end_ms', 'operating_revenue', 'net_profit_parent_company']:
        print(f"  {k}: {item0.get(k)}")

print('\nValuation Snapshot:')
val_data = fuyao['valuation_data']
print(json.dumps(val_data, ensure_ascii=False, indent=2))
