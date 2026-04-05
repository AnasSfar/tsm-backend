import pandas as pd

df = pd.read_csv('db/swift_top_100_history.csv')
print("Colonnes du CSV:")
print(df.columns.tolist())

print("\n" + "="*80)
print("\nTop 3 entries du CSV:\n")
df_latest = df[df['date'] == '2026-04-03'].head(3)
for idx, row in df_latest.iterrows():
    print(f"Rank {int(row['rank'])}: {row['title']}")
    print(f"  charts_points: {row['charts_points']} → display: {row['charts_points_display']}")
    print(f"  streams_points: {row['streams_points']} → display: {row['streams_points_display']}")
    print(f"  bonus_points: {row['bonus_points']} → display: {row['bonus_points_display']}")
    print(f"  points: {row['points']} → display: {row['points_display']}")
    print()
