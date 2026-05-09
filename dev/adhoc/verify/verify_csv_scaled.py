import pandas as pd

df = pd.read_csv('db/swift_top_100_history.csv')
df_latest = df[df['date'] == '2026-04-03'].head(5)

print("Top 5 entries in CSV with scaled points:\n")
print(f"{'Rank':<5} {'Title':<30} {'Points':<8} {'Charts':<8} {'Streams':<8}")
print("=" * 65)

for idx, row in df_latest.iterrows():
    print(f"{int(row['rank']):<5} {row['title']:<30} {row['points']:<8.2f} {row['charts_points']:<8.2f} {row['streams_points']:<8.2f}")
