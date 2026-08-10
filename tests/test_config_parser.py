#!/usr/bin/env python3
"""Tests for YAML configuration parsing and validation."""

import textwrap

import pytest

from config_parser import load_config, validate_mailbox_names

from tests.fixtures.synthetic import BUYER_NIP

MINIMAL_CONFIG = textwrap.dedent(f"""
    anthropic:
      api_key: "test-key-not-a-real-credential"
    nip: "{BUYER_NIP}"
    search:
      days_back: 30
      keywords:
        - faktura
    mailboxes:
      primary:
        host: imap.example.com
        port: 993
        username: user@example.com
        password: not-a-real-password
    output:
      main_directory: ./invoices
      uncertain_directory: ./invoices/uncertain
      log_file: ./app.log
    """)


def write_config(tmp_path, body: str) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return str(path)


class TestValidConfig:
    def test_parses_every_required_section(self, tmp_path):
        config = load_config(write_config(tmp_path, MINIMAL_CONFIG))

        assert config.api_key == "test-key-not-a-real-credential"
        assert config.nip == BUYER_NIP
        assert config.search.days_back == 30
        assert config.search.keywords == ["faktura"]
        assert config.output.main_directory == "./invoices"

    def test_builds_mailbox_objects_keyed_by_name(self, tmp_path):
        config = load_config(write_config(tmp_path, MINIMAL_CONFIG))

        assert set(config.mailboxes) == {"primary"}
        mailbox = config.mailboxes["primary"]
        assert mailbox.name == "primary"
        assert mailbox.host == "imap.example.com"
        assert mailbox.port == 993
        assert mailbox.use_ssl is True

    def test_coerces_port_to_int(self, tmp_path):
        body = MINIMAL_CONFIG.replace("port: 993", 'port: "993"')
        config = load_config(write_config(tmp_path, body))
        assert config.mailboxes["primary"].port == 993

    def test_strips_whitespace_around_nip(self, tmp_path):
        body = MINIMAL_CONFIG.replace(f'nip: "{BUYER_NIP}"', f'nip: "  {BUYER_NIP}  "')
        config = load_config(write_config(tmp_path, body))
        assert config.nip == BUYER_NIP


class TestOptionalSections:
    def test_supplies_default_prompts_when_absent(self, tmp_path):
        config = load_config(write_config(tmp_path, MINIMAL_CONFIG))
        assert "{nip}" in config.prompts.nip_check
        assert config.prompts.categorization.strip() != ""

    def test_uses_supplied_prompts_when_present(self, tmp_path):
        body = MINIMAL_CONFIG + textwrap.dedent("""
            prompts:
              nip_check: "custom {nip} check"
              categorization: "custom categorization"
            """)
        config = load_config(write_config(tmp_path, body))
        assert config.prompts.nip_check == "custom {nip} check"
        assert config.prompts.categorization == "custom categorization"

    def test_defaults_blacklist_to_empty(self, tmp_path):
        config = load_config(write_config(tmp_path, MINIMAL_CONFIG))
        assert config.filter.blacklist_keywords == []

    def test_reads_blacklist_keywords(self, tmp_path):
        body = MINIMAL_CONFIG + textwrap.dedent("""
            filter:
              blacklist_keywords:
                - reklama
                - newsletter
            """)
        config = load_config(write_config(tmp_path, body))
        assert config.filter.blacklist_keywords == ["reklama", "newsletter"]

    def test_ksef_absent_yields_none(self, tmp_path):
        config = load_config(write_config(tmp_path, MINIMAL_CONFIG))
        assert config.ksef is None

    def test_ksef_present_is_parsed_with_default_environment(self, tmp_path):
        body = MINIMAL_CONFIG + textwrap.dedent("""
            ksef:
              token: "not-a-real-ksef-token"
            """)
        config = load_config(write_config(tmp_path, body))
        assert config.ksef is not None
        assert config.ksef.token == "not-a-real-ksef-token"
        assert config.ksef.environment == "production"

    def test_ksef_environment_can_be_overridden(self, tmp_path):
        body = MINIMAL_CONFIG + textwrap.dedent("""
            ksef:
              token: "not-a-real-ksef-token"
              environment: "test"
            """)
        config = load_config(write_config(tmp_path, body))
        assert config.ksef.environment == "test"


class TestValidationFailures:
    def test_missing_file_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config(str(tmp_path / "absent.yaml"))

    def test_malformed_yaml_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError, match="Invalid YAML"):
            load_config(write_config(tmp_path, "anthropic: [unclosed\n"))

    def test_missing_top_level_keys_are_all_named(self, tmp_path):
        with pytest.raises(ValueError, match="Missing required config keys") as exc:
            load_config(write_config(tmp_path, 'nip: "1"\n'))
        message = str(exc.value)
        for key in ("anthropic", "search", "mailboxes", "output"):
            assert key in message

    def test_missing_api_key_is_reported(self, tmp_path):
        body = MINIMAL_CONFIG.replace('api_key: "test-key-not-a-real-credential"', 'other: "value"')
        with pytest.raises(ValueError, match="Missing 'api_key'"):
            load_config(write_config(tmp_path, body))

    def test_empty_nip_is_rejected(self, tmp_path):
        body = MINIMAL_CONFIG.replace(f'nip: "{BUYER_NIP}"', 'nip: ""')
        with pytest.raises(ValueError, match="NIP cannot be empty"):
            load_config(write_config(tmp_path, body))

    def test_empty_mailboxes_section_is_rejected(self, tmp_path):
        body = MINIMAL_CONFIG.replace(
            textwrap.dedent("""
                mailboxes:
                  primary:
                    host: imap.example.com
                    port: 993
                    username: user@example.com
                    password: not-a-real-password
                """).strip(),
            "mailboxes:",
        )
        with pytest.raises(ValueError, match="At least one mailbox"):
            load_config(write_config(tmp_path, body))

    def test_mailbox_missing_keys_names_the_mailbox(self, tmp_path):
        body = MINIMAL_CONFIG.replace("    port: 993\n", "")
        assert "port: 993" not in body, "fixture edit failed to remove the port line"
        with pytest.raises(ValueError, match="Missing keys in mailbox 'primary'") as exc:
            load_config(write_config(tmp_path, body))
        assert "port" in str(exc.value)

    def test_ksef_without_token_is_rejected(self, tmp_path):
        body = MINIMAL_CONFIG + textwrap.dedent("""
            ksef:
              environment: "test"
            """)
        with pytest.raises(ValueError, match="Missing 'token'"):
            load_config(write_config(tmp_path, body))


class TestValidateMailboxNames:
    def test_none_returns_every_configured_mailbox(self, tmp_path):
        config = load_config(write_config(tmp_path, MINIMAL_CONFIG))
        assert validate_mailbox_names(config, None) == ["primary"]

    def test_known_names_pass_through_unchanged(self, tmp_path):
        config = load_config(write_config(tmp_path, MINIMAL_CONFIG))
        assert validate_mailbox_names(config, ["primary"]) == ["primary"]

    def test_unknown_name_is_rejected_and_lists_alternatives(self, tmp_path):
        config = load_config(write_config(tmp_path, MINIMAL_CONFIG))
        with pytest.raises(ValueError, match="Unknown mailboxes") as exc:
            validate_mailbox_names(config, ["typo"])
        assert "primary" in str(exc.value)
