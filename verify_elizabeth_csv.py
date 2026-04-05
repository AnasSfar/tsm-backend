import pandas as pd

df = pd.read_csv('db/swift_top_100_history.csv')
df_latest = df[df['date'] == '2026-04-03']

print(f'Latest date entries: {len(df_latest)}')
print('\nTop 5 entries for 2026-04-03:')
for idx, row in df_latest.head(5).iterrows():
    print(f"Rank {int(row['rank'])}: {row['title']}")
    print(f"  charts_points: {row['charts_points']}, streams_points: {row['streams_points']}")
    print(f"  weekly_unfiltered_streams: {int(row['weekly_unfiltered_streams'])}")
    print()

# Search for Elizabeth Taylor
for idx, row in df_latest.iterrows():
    if 'elizabeth' in row['title'].lower():
        print(f"Found Elizabeth Taylor at Rank {int(row['rank'])}: {row['title']}")
        print(f"  charts_points: {row['charts_points']}, streams_points: {row['streams_points']}")
        break
