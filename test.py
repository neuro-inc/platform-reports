from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from google.oauth2.service_account import Credentials
from googleapiclient import discovery

GOOGLE_COMPUTE_ENGINE_ID = "services/6F81-5844-456A"


def get_service_skus() -> Iterator[dict[str, Any]]:
    next_page_token: str | None = ""
    while next_page_token is not None:
        response = list_service_skus(next_page_token)
        for sku in response["skus"]:
            if (
                sku["category"]["resourceFamily"] == "Compute"
                and sku["category"]["usageType"] in ("OnDemand", "Preemptible")
                and region in sku["serviceRegions"]
            ):
                yield sku
        next_page_token = response.get("nextPageToken") or None


def list_service_skus(next_page_token: str) -> Any:
    request = (
        client.services()
        .skus()
        .list(
            parent=GOOGLE_COMPUTE_ENGINE_ID,
            currencyCode="USD",
            pageToken=next_page_token,
        )
    )
    return request.execute()


sa_credentials = Credentials.from_service_account_file("key.json")
client = discovery.build("cloudbilling", "v1", credentials=sa_credentials)
region = "us-central1"

for sku in get_service_skus():
    description = sku["description"].lower()
    description_words = set(description.split())
    usage_type = sku["category"]["usageType"].lower()

    if usage_type != "ondemand":
        continue
    if "n2" not in description_words:
        continue
    if not description_words.intersection(("core", "cpu", "vcpu")):
        continue
    if description_words.intersection(("sole", "tenancy", "custom")):
        continue

    print(description)
    print(json.dumps(sku, indent=2))
    print()
