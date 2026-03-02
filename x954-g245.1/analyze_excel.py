import pandas as pd
import sys

try:
    file_path = 'neon_agent_copy.xlsx'
    print(f"Analyzing {file_path}...")
    xl = pd.ExcelFile(file_path)
    
    print(f"\nSheet names: {xl.sheet_names}")
    
    for sheet in xl.sheet_names:
        print(f"\n--- Sheet: {sheet} ---")
        try:
            df = pd.read_excel(xl, sheet_name=sheet, nrows=5)
            print(df.to_markdown(index=False))
            print(f"\nColumns: {list(df.columns)}")
            print(f"Shape (sample): {df.shape}")
        except Exception as e:
            print(f"Error reading sheet {sheet}: {e}")

except Exception as e:
    print(f"Error analyzing file: {e}")
