"""Synthetic requirements and independent acceptance checks for live pilot runs.

Only requirements are sent to the model. Check source is inserted after the mission
terminates; checks never ask a model to grade its own work or provide a prepared fix.
"""

CASES = {
    "slugify": {
        "requirements": (
            "Deliver a small Python utility in root app.py exposing slugify(text: str) -> str. "
            "Normalize Unicode with NFKD, remove combining marks, lowercase ASCII letters, "
            "replace each run of non-ASCII-alphanumeric characters with one hyphen, and "
            "strip leading/trailing hyphens. Examples: 'Hello World' -> 'hello-world'; "
            "'Café déjà vu' -> 'cafe-deja-vu'; empty or punctuation-only input -> ''. "
            "Use standard library only, include pytest tests and a README. No web server "
            "is needed. Finish with actual QA verification and a short launch draft."
        ),
        "checks": """import pytest
from app import slugify

@pytest.mark.parametrize(('value', 'expected'), [
    ('Hello World', 'hello-world'), ('Café déjà vu', 'cafe-deja-vu'),
    ('', ''), (' !!! ', ''), ('a___b--c', 'a-b-c'), ('  Trim  ', 'trim'),
    ('A1 B2', 'a1-b2'), ('naïve façade', 'naive-facade'),
    ('one\\ntwo\\tthree', 'one-two-three'), ('中文', ''),
    ('ＡＢＣ １２３', 'abc-123'), ('rock & roll', 'rock-roll'),
])
def test_independent_slug(value, expected):
    assert slugify(value) == expected
""",
    },
    "invoice": {
        "requirements": (
            "Deliver root app.py exposing invoice_total(prices: list[str], tax_rate: str) "
            "-> str. Treat prices and tax_rate as decimal strings, never binary floats. "
            "Sum prices, multiply by (1 + tax_rate), round once at the end to two decimal "
            "places using ROUND_HALF_UP, and return a fixed two-decimal string. Empty "
            "prices yields '0.00'. Reject negative or non-finite prices/rates and rates "
            "greater than 1 with ValueError. Use standard library only; include pytest "
            "tests, README, QA verification, and a short launch draft. No server needed."
        ),
        "checks": """import pytest
from app import invoice_total

@pytest.mark.parametrize(('prices', 'rate', 'expected'), [
    (['0.10', '0.20'], '0', '0.30'), (['0.05'], '0.1', '0.06'),
    (['0.005', '0.005'], '0', '0.01'), ([], '0.2', '0.00'),
    (['10.00', '2.50'], '0.2', '15.00'), (['1.005'], '0', '1.01'),
])
def test_independent_total(prices, rate, expected):
    assert invoice_total(prices, rate) == expected

@pytest.mark.parametrize(('prices', 'rate'), [
    (['-1'], '0'), (['NaN'], '0'), (['Infinity'], '0'),
    (['1'], '-0.1'), (['1'], '1.1'), (['1'], 'NaN'), (['1'], 'Infinity'),
])
def test_independent_rejection(prices, rate):
    with pytest.raises(ValueError):
        invoice_total(prices, rate)
""",
    },
    "csv_summary": {
        "requirements": (
            "Deliver root app.py exposing summarize_csv(text: str) -> dict[str, int]. "
            "Parse CSV with a required header containing category and amount (in any "
            "order); ignore extra columns. Trim category/amount whitespace. Sum integer "
            "amounts by category; negatives are allowed; category is case-sensitive. "
            "Skip completely blank rows. Header-only input yields {}. Reject missing "
            "required headers, empty category, missing amount, or non-integer amount "
            "with ValueError. Correctly parse quoted commas and CRLF. Standard library "
            "only. Include pytest tests, README, QA and a short launch draft; no server."
        ),
        "checks": """import pytest
from app import summarize_csv

@pytest.mark.parametrize(('text', 'expected'), [
    ('category,amount\\na,2\\na,3\\nb,-1\\n', {'a': 5, 'b': -1}),
    ('amount,category,unused\\r\\n2, a ,x\\r\\n', {'a': 2}),
    ('category,amount\\n"a,b",4\\n', {'a,b': 4}),
    ('category,amount\\n', {}),
    ('category,amount\\n\\na,1\\n\\n', {'a': 1}),
    ('category,amount\\na,0\\nA,2\\n', {'a': 0, 'A': 2}),
])
def test_independent_csv(text, expected):
    assert summarize_csv(text) == expected

@pytest.mark.parametrize('text', [
    '', 'name,amount\\na,1\\n', 'category,value\\na,1\\n',
    'category,amount\\n,1\\n', 'category,amount\\na,\\n',
    'category,amount\\na,1.2\\n', 'category,amount\\na\\n',
])
def test_independent_csv_rejection(text):
    with pytest.raises(ValueError):
        summarize_csv(text)
""",
    },
}
