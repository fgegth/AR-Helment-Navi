from voice_auth import verify
ok = verify("owner", 0.70, 2)
print("MATCH" if ok else "NO MATCH")
