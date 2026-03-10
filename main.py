import json

from bet365 import Bet365AndroidSession

with open("config.json", encoding="utf8") as fp:
    config = json.load(fp)

print("Fetching soccer page using android api")

session = Bet365AndroidSession(
    config["api_url"],
    config["api_key"],
    proxy=config["proxy"] or None,
    verify=False,
    host="www.bet365.com",
)

session.go_homepage()

sports = session.extract_available_sports()
tennis = next(filter(lambda m: m.name == "Soccer", sports))
session.get_sport_homepage(tennis)
if session.zap_thread is not None:
    session.zap_thread.join()
