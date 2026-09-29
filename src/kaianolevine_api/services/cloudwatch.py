"""The CloudWatch client the request-metrics middleware publishes through.

The middleware lives in common-python-utils and takes no dependency on
boto3; it asks for a client on its first publish. This is the API's
answer: the same AWS identity that enqueues cog jobs, whose IAM policy also
allows cloudwatch:PutMetricData in the MiniAppPolis/Api namespace and
nowhere else (mini-app-polis/infra, account.tf).

Built once, on the flush thread, never on a request.
"""

from __future__ import annotations

from typing import Any

import boto3

from ..config import Settings
from .job_queue import producer_credentials


def client_factory(settings: Settings) -> Any:
    """A CloudWatch client for this API's region and credentials."""
    return boto3.client(
        "cloudwatch",
        region_name=settings.AWS_REGION,
        **producer_credentials(settings),
    )
