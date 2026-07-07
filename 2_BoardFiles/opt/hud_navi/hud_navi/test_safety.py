from road_safety import analyze_route
r = analyze_route([
    {"road": "长安街", "instruction": "直行", "distance": 500},
    {"road": "南池子自行车道", "instruction": "右转", "distance": 200}
])
print(r["summary"])
print("current:", r["current_level"])
print("upcoming:", r["upcoming"])
