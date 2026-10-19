from pathlib import Path
import re

project = Path(r"F:\PythonProjects\CodePulseDemo")

before_file = project / "evidence" / "PaymentService.before.java"
after_file = (
    project / "src" / "main" / "java" / "com" / "codepulse"
    / "demo" / "service" / "PaymentService.java"
)

before = before_file.read_text(encoding="utf-8")
after = after_file.read_text(encoding="utf-8")

pattern = re.compile(
    r"return\s+order\.getAmount\(\)\s*(>=|>|<=|<|==|!=)\s*(\d+(?:\.\d+)?)\s*;"
)

old_match = pattern.search(before)
new_match = pattern.search(after)

if not old_match or not new_match:
    print("Could not recognize the payment conditions.")
    raise SystemExit(1)

old_operator, old_threshold = old_match.groups()
new_operator, new_threshold = new_match.groups()

old_threshold = float(old_threshold)
new_threshold = float(new_threshold)

print("Old condition:", old_operator, old_threshold)
print("New condition:", new_operator, new_threshold)

def evaluate(amount, operator, threshold):
    if operator == ">":
        return amount > threshold
    if operator == ">=":
        return amount >= threshold
    if operator == "<":
        return amount < threshold
    if operator == "<=":
        return amount <= threshold
    if operator == "==":
        return amount == threshold
    if operator == "!=":
        return amount != threshold
    raise ValueError("Unsupported operator: " + operator)

print("\n=== Behavioral Comparison ===")
for amount in [0, 1, 50, 99, 100, 101, 150]:
    old_result = evaluate(amount, old_operator, old_threshold)
    new_result = evaluate(amount, new_operator, new_threshold)

    if old_result != new_result:
        print(
            f"Amount {amount}: "
            f"before={old_result}, after={new_result} "
            f"<-- behavior changed"
        )
