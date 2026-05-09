import pandas as pd

df = pd.read_csv('db/swift_top_100_history.csv')
df = df[df['week_end'] == '2026-04-03']

print('Top 5 entries for week_end=2026-04-03:')
for idx, row in df.head(5).iterrows():
    print(f"Rank {int(row['rank'])}: {row['title']}")
    print(f"  charts_points: {row['charts_points']}, streams_points: {row['streams_points']}")
    print(f"  weekly_unfiltered_streams: {int(row['weekly_unfiltered_streams'])}")
    print(f"  weekly_filtered_streams: {int(row['weekly_filtered_streams'])}")
    print()
