import json
from pathlib import Path

project = Path(r"F:\PythonProjects\CodePulseDemo")
file_path = project / "evidence" / "change-analysis.json"

with file_path.open("r", encoding="utf-8") as file:
    change = json.load(file)

print("File:", change["file"])
print("Method:", change["method"])
print("Change type:", change["changeType"])
print("Before:", change["oldCondition"])
print("After:", change["newCondition"])
print("Potential impacts:")
for impact in change["potentialImpact"]:
    print("-", impact)

print("Direct caller:", change["directCaller"])
print("Indirect caller:", change["indirectCaller"])
