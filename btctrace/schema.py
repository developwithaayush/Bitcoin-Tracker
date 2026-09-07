"""Canonical record schema shared by the generator, the parsers and the models."""

# Column order is canonical: every parser must produce exactly this, in this order.
COLUMNS = [
    "timestamp",
    "src_ip",
    "dst_ip",
    "src_port",
    "dst_port",
    "txid",
    "input_addresses",
    "output_addresses",
    "input_amounts",
    "output_amounts",
    "fee",
    "script_type",
    "geo_country",
    "asn",
]

# Columns carrying a variable-length list per row. CSV flattens these with LIST_SEP,
# JSON keeps them as arrays, XML nests them as child elements.
LIST_COLUMNS = ["input_addresses", "output_addresses", "input_amounts", "output_amounts"]
AMOUNT_COLUMNS = ["input_amounts", "output_amounts"]

LIST_SEP = ";"

# Bitcoin amounts are 8-decimal fixed point. Rounding every amount to SATOSHI_DP at
# generation is what lets CSV (decimal text) and JSON (float repr) round-trip to the
# identical double, so the three formats converge exactly rather than approximately.
SATOSHI_DP = 8

SCRIPT_TYPES = ["p2pkh", "p2sh", "p2wpkh", "p2tr"]
