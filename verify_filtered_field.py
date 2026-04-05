import pandas as pd
import math

df = pd.read_csv('db/swift_top_100_history.csv')
print('Column names:')
print(df.columns.tolist())

df_latest = df[df['date'] == '2026-04-03']
for idx, row in df_latest.iterrows():
    if 'elizabeth' in row['title'].lower():
        print(f"\nRank {int(row['rank'])}: {row['title']}")
        print(f"  weekly_unfiltered_streams: {int(row['weekly_unfiltered_streams'])}")
        filtered_val = row['weekly_filtered_streams']
        if pd.isna(filtered_val):
            print(f"  weekly_filtered_streams: NaN (not populated)")
        else:
            print(f"  weekly_filtered_streams: {int(filtered_val)}")
        print(f"  charts_points: {row['charts_points']}, streams_points: {row['streams_points']}")
        
        # Calculate what filtered should be based on points
        calculated_filtered = row['charts_points'] * 10000
        print(f"  calculated filtered (from charts_points): {int(calculated_filtered)}")
        break
