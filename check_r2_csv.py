#!/usr/bin/env python3
"""Vérifier si le CSV sur R2 a les données correctes"""
import sys
import boto3
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(str(Path(__file__).resolve().parents[0] / ".env"))

import os

account_id = os.getenv("R2_ACCOUNT_ID")
access_key = os.getenv("R2_ACCESS_KEY_ID")
secret_key = os.getenv("R2_SECRET_ACCESS_KEY")
bucket = os.getenv("R2_BUCKET", "taylor-data")

client = boto3.client(
    "s3",
    endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
    aws_access_key_id=access_key,
    aws_secret_access_key=secret_key,
    region_name="auto",
)

print("Téléchargement du CSV depuis R2...")
try:
    resp = client.get_object(Bucket=bucket, Key="data/charts_global.csv")
    content = resp['Body'].read().decode('utf-8')
    
    lines = content.split('\n')
    print(f"Header: {lines[0]}\n")
    
    # Chercher Cruel Summer le 24 mars
    print("Cherchant Cruel Summer 2026-03-24...")
    for line in lines:
        if "2026-03-24" in line and "Cruel Summer" in line:
            print(f"Trouvé: {line}")
            break
    else:
        print("❌ Non trouvée!")
        
        # Chercher d'autres dates de Cruel Summer
        print("\nAu lieu de ça, voici Cruel Summer d'autres dates:")
        count = 0
        for line in lines:
            if "Cruel Summer" in line:
                print(f"  {line[:100]}")
                count += 1
                if count >= 5:
                    break
                
except Exception as e:
    print(f"❌ Erreur: {e}")
