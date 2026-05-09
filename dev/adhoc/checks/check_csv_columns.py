import pandas as pd

df = pd.read_csv('db/swift_top_100_history.csv')
print('Column names:')
print(df.columns.tolist())
print('\nFirst few rows:')
print(df.head(10))
