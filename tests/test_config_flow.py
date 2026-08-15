"""Tests for the Disklavier config flow.

The quality scale requires 100% coverage of this module, so every branch here is
deliberate rather than incidental.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from aiodisklavier import (
    DisklavierConnectionError,
    DisklavierResponseError,
    StaticInfo,
)
from homeassistant.config_entries import SOURCE_SSDP, SOURCE_USER
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.disklavier.const import DOMAIN

from .conftest import DISKLAVIER_ID, HOST

NEW_HOST = "192.168.1.99"


def _ssdp(
    location: str | None = f"http://{HOST}:49152/gatedesc.xml",
) -> SsdpServiceInfo:
    """Build an SSDP discovery payload of the shape the piano produces."""
    return SsdpServiceInfo(
        ssdp_usn=f"uuid:{DISKLAVIER_ID}",
        ssdp_st="urn:schemas-upnp-org:device:Disklavier:1",
        ssdp_location=location,
        upnp={"friendlyName": DISKLAVIER_ID},
    )


# ----------------------------------------------------------------------
# User flow
# ----------------------------------------------------------------------


async def test_user_flow(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    """A piano entered by hand is probed and added."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Disklavier PRO"
    assert result["data"] == {CONF_HOST: HOST}
    assert result["result"].unique_id == DISKLAVIER_ID


@pytest.mark.parametrize(
    ("side_effect", "expected"),
    [
        (DisklavierConnectionError("unreachable"), "cannot_connect"),
        (DisklavierResponseError("not a piano"), "invalid_response"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_flow_errors_then_recovers(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    side_effect: Exception,
    expected: str,
    static_info: StaticInfo,
) -> None:
    """Each failure is shown on the form, and the user can then succeed."""
    mock_client.async_get_static_info.side_effect = side_effect

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}

    # The form stays usable: fixing the cause completes the flow.
    mock_client.async_get_static_info.side_effect = None
    mock_client.async_get_static_info.return_value = static_info
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_duplicate_aborts(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The same piano cannot be added twice, and its address is refreshed."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: NEW_HOST}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data[CONF_HOST] == NEW_HOST


# ----------------------------------------------------------------------
# SSDP discovery
# ----------------------------------------------------------------------


async def test_ssdp_flow(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    """A discovered piano is confirmed by the user, then added."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_SSDP}, data=_ssdp()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"
    assert result["description_placeholders"] == {"model": "PRO", "host": HOST}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: HOST}


async def test_ssdp_without_location_aborts(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """A discovery with no usable address cannot be acted on."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_SSDP}, data=_ssdp(location=None)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_ssdp_unreachable_aborts(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """Something announced itself but did not answer; nothing to offer the user."""
    mock_client.async_get_static_info.side_effect = DisklavierConnectionError("nope")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_SSDP}, data=_ssdp()
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_ssdp_updates_host_of_existing_entry(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Rediscovery tracks a piano that moved to a new address."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_SSDP},
        data=_ssdp(location=f"http://{NEW_HOST}:49152/gatedesc.xml"),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data[CONF_HOST] == NEW_HOST


# ----------------------------------------------------------------------
# Reconfigure
# ----------------------------------------------------------------------


async def test_reconfigure(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """An entry can be pointed at a new address for the same piano."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: NEW_HOST}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_HOST] == NEW_HOST
    await hass.async_block_till_done()


async def test_reconfigure_error_then_recovers(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    static_info: StaticInfo,
) -> None:
    """A bad address is reported on the form rather than saved."""
    mock_config_entry.add_to_hass(hass)
    mock_client.async_get_static_info.side_effect = DisklavierConnectionError("nope")

    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: NEW_HOST}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert mock_config_entry.data[CONF_HOST] == HOST

    mock_client.async_get_static_info.side_effect = None
    mock_client.async_get_static_info.return_value = static_info
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: NEW_HOST}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    # A successful reconfigure reloads the entry; let that finish before teardown.
    await hass.async_block_till_done()


async def test_reconfigure_refuses_a_different_piano(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    static_info: StaticInfo,
) -> None:
    """Repointing an entry at another instrument would orphan its history."""
    mock_config_entry.add_to_hass(hass)
    other = StaticInfo(
        api_version=static_info.api_version,
        api_revision=static_info.api_revision,
        disklavier_id="DKV111111111111",
        region=static_info.region,
        version=static_info.version,
        model=static_info.model,
        piano_type=static_info.piano_type,
    )
    mock_client.async_get_static_info.return_value = other

    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: NEW_HOST}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_piano"
    assert mock_config_entry.data[CONF_HOST] == HOST
