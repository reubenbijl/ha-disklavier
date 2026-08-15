"""Config flow for the Yamaha Disklavier integration."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol
from aiodisklavier import (
    Disklavier,
    DisklavierConnectionError,
    DisklavierError,
    StaticInfo,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})


class DisklavierConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for a Yamaha Disklavier."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._host: str | None = None
        self._static_info: StaticInfo | None = None

    async def _async_validate(self, host: str) -> tuple[StaticInfo | None, str | None]:
        """Probe a host, returning either its identity or an error key.

        Fetching static_info proves three things at once: the address is reachable, it
        speaks the open API, and it is a Disklavier rather than some other device.
        """
        client = Disklavier(host, async_get_clientsession(self.hass))
        try:
            return await client.async_get_static_info(), None
        except DisklavierConnectionError:
            return None, "cannot_connect"
        except DisklavierError:
            return None, "invalid_response"
        except Exception:
            _LOGGER.exception("Unexpected error connecting to Disklavier at %s", host)
            return None, "unknown"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow started by the user."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            static_info, error = await self._async_validate(host)
            if static_info is not None:
                await self.async_set_unique_id(static_info.disklavier_id)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                return self.async_create_entry(
                    title=f"Disklavier {static_info.model}",
                    data={CONF_HOST: host},
                )
            assert error is not None
            errors["base"] = error

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user point an existing entry at a new address.

        Useful when the piano's DHCP lease changes and it is not rediscovered: the entry
        keeps its entities and history rather than having to be removed and re-added.
        """
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            host = user_input[CONF_HOST]
            static_info, error = await self._async_validate(host)
            if static_info is not None:
                # Refuse to repoint an entry at a *different* piano: its entities and
                # history belong to the instrument the entry was created for.
                await self.async_set_unique_id(static_info.disklavier_id)
                self._abort_if_unique_id_mismatch(reason="wrong_piano")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_HOST: host}
                )
            assert error is not None
            errors["base"] = error

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, {CONF_HOST: entry.data[CONF_HOST]}
            ),
            errors=errors,
        )

    async def async_step_ssdp(
        self, discovery_info: SsdpServiceInfo
    ) -> ConfigFlowResult:
        """Handle a piano discovered over SSDP."""
        host = urlparse(discovery_info.ssdp_location or "").hostname
        if not host:
            return self.async_abort(reason="cannot_connect")

        static_info, _ = await self._async_validate(host)
        if static_info is None:
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(static_info.disklavier_id)
        # Rediscovery doubles as address tracking: an already-configured piano that comes
        # back on a new IP has its entry updated instead of being offered again.
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        self._host = host
        self._static_info = static_info
        self.context["title_placeholders"] = {"name": f"Disklavier {static_info.model}"}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to confirm adding a discovered piano."""
        assert self._host is not None
        assert self._static_info is not None

        if user_input is not None:
            return self.async_create_entry(
                title=f"Disklavier {self._static_info.model}",
                data={CONF_HOST: self._host},
            )

        self._set_confirm_only()
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={
                "model": self._static_info.model,
                "host": self._host,
            },
        )
