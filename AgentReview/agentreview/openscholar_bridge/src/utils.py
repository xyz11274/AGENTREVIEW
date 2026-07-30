# AgentReview/agentreview/openscholar_bridge/src/utils.py

import jsonlines
import re
import csv
import os
import json

def load_jsonlines(file):
    try:
        with jsonlines.open(file, 'r') as jsonl_f:
            lst = [obj for obj in jsonl_f]
    except:
        lst = []
        with open(file) as f:
            for line in f:
                lst.append(json.loads(line))
    return lst
    
def save_file_jsonl(data, fp):
    with jsonlines.open(fp, mode='w') as writer:
        writer.write_all(data)

def extract_titles(text):
    # Regular expression pattern to match the titles
    pattern = r'\[\d+\]\s(.+)'  # Matches [number] followed by any characters
    # Find all matches of the pattern in the reference text
    if "References:" not in text:
        return []
    else:
        reference_text = text.split("References:")[1]
        print(reference_text)
        matches = re.findall(pattern, reference_text)
        return matches

def save_tsv_dict(data, fp, fields):
    # build dir
    dir_path = os.path.dirname(fp)
    os.makedirs(dir_path, exist_ok=True)

    # writing to csv file
    with open(fp, 'w') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fields, delimiter='\t',)
        writer.writeheader()
        writer.writerows(data)
