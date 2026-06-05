import tushare as ts
#tushare版本 1.4.24
token = "41a7a01862789fae3b7ff8d0577bf2ed13c127e52557610613b5aaba50e9"

pro = ts.pro_api(token)

pro._DataApi__token = token # 保证有这个代码，不然不可以获取
pro._DataApi__http_url = 'http://lianghua.nanyangqiankun.top'  # 保证有这个代码，不然不可以获取

dfc = pro.trade_cal(
    start_date="20250101",
    end_date="20251231"
)
print(dfc)
print(dfc.columns)
print(dfc.empty)

# #  正常使用（与官方API完全一致）
df = pro.daily(ts_code='000001.SZ', start_date='20250101', end_date='20250131')


print(df)
