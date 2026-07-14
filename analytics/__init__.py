"""Director analytics — read-only, feature-wise business analytics.

A standalone, live dashboard (served at GET /director) plus a set of read-only
JSON endpoints under /director/api/*, aggregating the existing kirana_oltp data
across nine feature domains: sales, customers (CRM), baskets, referrals, AI usage,
subscriptions, app engagement, footfall/schemes, and vision.

Access is gated by a dedicated read-only DIRECTOR_TOKEN (see analytics/auth.py);
the admin X-API-Key also authorizes so existing admin tooling can view it too.
Nothing here mutates data.
"""
