# Copyright (c) 2026, Trident SACCO and contributors
# For license information, please see license.txt
"""Page controller for /guarantor-confirm (token-protected, guest allowed)."""

no_cache = 1
sitemap = 0


def get_context(context):
	context.no_cache = 1
	context.title = "Guarantee request"
	return context
