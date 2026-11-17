with open('index.html', 'rb') as f:
    data = f.read()
    
# Find line 214 (index 213)
lines = data.split(b'\n')
for i in range(213, 218):
    if i < len(lines):
        print(f"Line {i+1}: {lines[i]!r}")