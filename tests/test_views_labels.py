"""Choosing the name a product is shown under.

A retailer prints its own furniture into the name field — a promotional price,
a stray bracket, an OCR artefact — and spells the same product differently
between visits. These are the three pure functions that decide what a reader
sees, tested apart from the database so each rule can be stated on its own.

**Every value here is invented and none resembles the corpus.** The shapes are
real and the names are not: `tools/scan_pii.py` has no rule for a product name,
so the only thing standing between a real basket and this file is the habit of
making them up. See `CONTRIBUTING.md`.

The hazards below are the ones that were actually measured, and two of them are
traps rather than cases:

* a name opening with a bare number is a SIZE, not a price, and stripping it
  leaves something that still reads like a product while being wrong
* a name CONTAINING deposit vocabulary is usually a drink; only a name that is
  deposit vocabulary end to end is a charge

`adapters/hmart/receipt.py::_is_furniture` carries the same lesson from the
other side, where matching the word `TENDER` alone deleted a real `GIFT CARD`
purchase.
"""

from unbagged.views import clean_label, is_non_product, label_for


class TestCleanLabel:
    def test_a_price_the_retailer_printed_into_the_name_comes_off(self):
        assert clean_label("$4.99 OAT MILK HALF GAL") == "OAT MILK HALF GAL"

    def test_a_leading_bare_number_is_a_size_and_stays(self):
        """The trap. A rule that strips any leading number was measured against
        the corpus: it matched seven names, five of them prices and two of them
        a bottle size followed by a unit. Eating the size leaves a name that is
        wrong and still plausible, which is worse than an ugly one.
        """
        assert clean_label("2.5 LITR 12 CRV") == "2.5 LITR 12 CRV"

    def test_a_percentage_survives(self):
        assert clean_label("2% MILK") == "2% MILK"

    def test_a_name_opening_with_a_digit_survives(self):
        assert clean_label("7UP CHERRY 12PK") == "7UP CHERRY 12PK"

    def test_leading_punctuation_comes_off(self):
        assert clean_label("(2) BAGELS SESAME") == "2) BAGELS SESAME"

    def test_a_name_that_is_only_punctuation_cleans_to_nothing(self):
        """Which is how the caller knows the row names no product at all."""
        assert clean_label("©") == ""

    def test_an_ordinary_name_is_returned_unchanged(self):
        assert clean_label("SOURDOUGH BOULE") == "SOURDOUGH BOULE"

    def test_a_price_and_punctuation_together_both_come_off(self):
        assert clean_label("$1.25 -CART FEE RETURN") == "CART FEE RETURN"


class TestIsNonProduct:
    def test_a_bare_deposit_word_is_a_charge(self):
        assert is_non_product("CRV") is True

    def test_deposit_vocabulary_end_to_end_is_a_charge(self):
        """The shape that motivated this. Once the printed price comes off,
        `$0.05 CRV DEPOSIT` is two words and both are vocabulary.

        A whole-name test against a single word was the first rule written for
        this and it dropped none of them, because none of them is one word.
        """
        assert is_non_product(clean_label("$0.05 CRV DEPOSIT")) is True

    def test_a_drink_whose_name_contains_the_word_is_a_product(self):
        """The other trap, and the expensive one: these carry codes and were
        bought repeatedly. Matching on containment takes them.
        """
        assert is_non_product("2.5 LITR 12 CRV") is False

    def test_a_product_whose_name_ends_in_tax_is_a_product(self):
        assert is_non_product("CIGARETTE USE TAX") is False

    def test_an_empty_label_is_not_claimed_either_way(self):
        """It names no product, but that is the empty rule's business, not this
        one's. Returning True here would make the two refusals impossible to
        tell apart in the count.
        """
        assert is_non_product("") is False

    def test_the_match_is_case_insensitive(self):
        assert is_non_product("crv deposit") is True


class TestLabelFor:
    def test_the_commonest_spelling_wins(self):
        assert label_for({"OAT MILK": 5, "OAT MLK": 1})[0] == "OAT MILK"

    def test_spellings_that_clean_alike_pool_their_counts(self):
        """Otherwise a price prefix splits one product's vote between two
        spellings and a third, rarer name can win on the split.
        """
        label, _ = label_for({"$4.99 OAT MILK": 3, "OAT MILK": 3, "OAT MLK": 5})

        assert label == "OAT MILK"

    def test_the_raw_spelling_comes_back_beside_the_label(self):
        """It is what every lookup matches on, so it has to be a string the
        retailer really printed rather than the cleaned one.
        """
        label, raw = label_for({"$4.99 OAT MILK": 2})

        assert label == "OAT MILK"
        assert raw == "$4.99 OAT MILK"

    def test_a_tie_resolves_the_same_way_every_time(self):
        """`max` over a counter returns whichever key was inserted first, and
        insertion order here comes from a GROUP BY with no ORDER BY. Two
        dictionaries carrying the same counts in a different order must not
        produce two different pages.
        """
        first = label_for({"ALPHA BREAD": 2, "BETA BREAD": 2})
        second = label_for({"BETA BREAD": 2, "ALPHA BREAD": 2})

        assert first == second

    def test_a_name_that_cleans_to_nothing_keeps_its_raw_form(self):
        """So the caller can still see there was a row and still refuse it."""
        assert label_for({"©": 2}) == ("©", "©")
