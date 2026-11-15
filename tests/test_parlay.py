from streaks.parlay import (
    Leg, american_to_decimal, best_parlays, decimal_to_american, legs_from_result,
    parse_american,
)


def test_odds_conversion_round_trips():
    for price in (-500, -150, -110, 100, 150, 400, 1000):
        assert decimal_to_american(american_to_decimal(price)) == price


def test_parse_american_requires_sign():
    assert parse_american("+150") == 150
    assert parse_american("-110") == -110
    try:
        parse_american("150")
        assert False, "should have raised"
    except ValueError:
        pass


def _leg(player, price, hits=10, games=10):
    return Leg(player=player, team="DEN", stat_label="Points", point=20.5, price=price, hits=hits, games=games)


def test_best_parlays_hits_requested_leg_count():
    legs = [_leg("A", -110), _leg("B", -110), _leg("C", -110), _leg("D", -110)]
    [p] = best_parlays(legs, target_price=400, num_legs=2, count=1)
    assert len(p.legs) == 2
    # two -110 legs combine close to +260 in decimal terms; sanity check it's in a plausible range
    assert 200 <= p.american <= 500


def test_best_parlays_one_leg_per_player():
    legs = [_leg("A", -110), _leg("A", 150)]  # same player twice
    parlays = best_parlays(legs, target_price=200, num_legs=2, count=5)
    assert parlays == []  # no valid 2-leg combo without reusing "A"


def test_best_parlays_prefers_higher_confidence_on_ties():
    legs = [_leg("A", 100, hits=10, games=10), _leg("B", 100, hits=5, games=10)]
    [p] = best_parlays(legs, target_price=100, num_legs=1, count=1)
    assert p.legs[0].player == "A"


def test_legs_from_result_dedupes_and_requires_book():
    result = {
        "teams": {"4": {"abbr": "DEN"}},
        "stats": [{
            "key": "pts", "label": "Points",
            "rows": [
                {"player": {"id": "p1", "short": "N. Jokic"}, "team": "4",
                 "book": {"point": 24.5, "over_price": -130, "hits": 4, "games": 5}},
                {"player": {"id": "p2", "short": "No Book"}, "team": "4", "book": None},
            ],
        }],
    }
    legs = legs_from_result(result)
    assert len(legs) == 1 and legs[0].player == "N. Jokic" and legs[0].price == -130
