conda activate traceguard
cd ~/Techorphic/Projects/traceguard
clear


# 1. Normal case

python run.py --events data/events.jsonl --tenant T1 --order O-201 --question "Was this order paid?" 2>/dev/null

clear

# 2. Attack case

python run.py --events data/events.jsonl --tenant T1 --order O-214 --question "Was this order paid?" 2>/dev/null

clear

# 3. Conflicting data case

python run.py --events data/events.jsonl --tenant T1 --order O-212 --question "Was this order paid?" 2>/dev/null

clear

# 4. Tests

python -m pytest -q
