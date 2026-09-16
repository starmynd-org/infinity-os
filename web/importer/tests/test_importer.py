from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from web.importer.attention import outcome_to_item
from web.importer.catalog import parse_catalog
from web.importer.errors import ImportRefusal
from web.importer.frontmatter import markdown_outcome
from web.importer.manifest import import_directory, parse_envelope, verify_stable_bytes
from web.importer.model import (
    Coverage,
    Loss,
    Outcome,
    PortableEntity,
    require_declared_losses,
)
from web.importer.swarm import plan_swarm
from web.importer.workflow import parse_workflow_json


def markdown(entity_id: str = "cmd.one", *, entity_type: str = "Command") -> bytes:
    return (
        "---\n"
        f"id: {entity_id}\n"
        f"type: {entity_type}\n"
        "name: One\n"
        "description: Does one bounded thing\n"
        "namespace: test\n"
        "---\n"
        "# One\n\nDo one bounded thing.\n"
    ).encode()


def envelope(
    members: list[dict[str, str]],
    *,
    producer: str = "brain-directory/1.0",
    complete: bool = True,
    missing: list[str] | None = None,
) -> dict[str, object]:
    return {
        "contract_version": "ESTATE/1.0",
        "producer": producer,
        "source_root_id": "test-estate",
        "generated_at": "2026-09-09T12:00:00Z",
        "complete": complete,
        "members": members,
        "member_count": len(members),
        "source_revision": "a" * 40,
        "missing": [] if missing is None else missing,
        "control": {
            "name": "source-root-readable",
            "fired": True,
            "evidence": "exporter read its configured source root",
        },
        "empty_reason": "controlled empty export" if not members else "",
    }


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RefusalPlantTests(unittest.TestCase):
    def assert_refusal(self, code: str, call) -> ImportRefusal:
        with self.assertRaises(ImportRefusal) as caught:
            call()
        self.assertEqual(code, caught.exception.code)
        self.assertTrue(caught.exception.member)
        self.assertTrue(caught.exception.why)
        self.assertTrue(caught.exception.instead)
        return caught.exception

    def test_unknown_producer(self) -> None:
        self.assert_refusal(
            "UNKNOWN-PRODUCER",
            lambda: parse_envelope(envelope([], producer="provider-live/1.0")),
        )

    def test_complete_manifest_cannot_name_missing_material(self) -> None:
        self.assert_refusal(
            "EXPORT-INCOMPLETE",
            lambda: parse_envelope(envelope([], missing=["agents"])),
        )

    def test_partial_manifest_must_name_missing_material(self) -> None:
        self.assert_refusal(
            "PARTIAL-EXPORT",
            lambda: parse_envelope(envelope([], complete=False)),
        )

    def test_member_count_mismatch(self) -> None:
        payload = envelope([])
        payload["member_count"] = 1
        self.assert_refusal("COUNT-MISMATCH", lambda: parse_envelope(payload))

    def test_positive_control_must_fire(self) -> None:
        payload = envelope([])
        payload["control"]["fired"] = False
        self.assert_refusal("COUNT-MISMATCH", lambda: parse_envelope(payload))

    def test_path_escape(self) -> None:
        members = [{"path": "../secret.md", "sha256": "0" * 64}]
        self.assert_refusal("PATH-ESCAPE", lambda: parse_envelope(envelope(members)))

    def test_not_utf8(self) -> None:
        self.assert_refusal(
            "NOT-UTF8",
            lambda: markdown_outcome(
                data=b"\xff",
                member="bad.md",
                producer="brain-directory/1.0",
                source_revision="a" * 40,
            ),
        )

    def test_digest_mismatch(self) -> None:
        self.assert_refusal(
            "DIGEST-MISMATCH",
            lambda: verify_stable_bytes(
                member="one.md", first=b"one", second=b"one", expected_sha256="0" * 64
            ),
        )

    def test_source_moved(self) -> None:
        self.assert_refusal(
            "SOURCE-MOVED-DURING-IMPORT",
            lambda: verify_stable_bytes(
                member="one.md", first=b"one", second=b"two", expected_sha256=digest(b"one")
            ),
        )

    def test_malformed_frontmatter(self) -> None:
        self.assert_refusal(
            "MALFORMED-FRONTMATTER",
            lambda: markdown_outcome(
                data=b"---\nid: one\ntype: Command\n",
                member="one.md",
                producer="brain-directory/1.0",
                source_revision="a" * 40,
            ),
        )

    def test_duplicate_frontmatter_key(self) -> None:
        data = b"---\nid: one\nid: two\ntype: Command\ndescription: bounded\n---\nDo it.\n"
        self.assert_refusal(
            "MALFORMED-FRONTMATTER",
            lambda: markdown_outcome(
                data=data,
                member="one.md",
                producer="brain-directory/1.0",
                source_revision="a" * 40,
            ),
        )

    def test_malformed_json(self) -> None:
        self.assert_refusal("MALFORMED-JSON", lambda: parse_workflow_json(b"{", "flow.json"))

    def test_huge_workflow_number_is_structured_refusal(self) -> None:
        data = (
            b'{"nodes":[{"id":"node-one"}],"connections":{},"extra":'
            + b"9" * 4301
            + b"}"
        )
        self.assert_refusal(
            "MALFORMED-JSON", lambda: parse_workflow_json(data, "flow.json")
        )

    def test_huge_manifest_number_is_structured_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "estate-export.json").write_bytes(
                b'{"status":' + b"9" * 4301 + b"}"
            )
            self.assert_refusal("MALFORMED-JSON", lambda: import_directory(root))

    def test_empty_workflow(self) -> None:
        data = json.dumps({"nodes": [], "connections": {}}).encode()
        self.assert_refusal("EMPTY-WORKFLOW", lambda: parse_workflow_json(data, "flow.json"))

    def test_unknown_entity_type(self) -> None:
        self.assert_refusal(
            "UNKNOWN-ENTITY-TYPE",
            lambda: markdown_outcome(
                data=markdown(entity_type="Event"),
                member="event.md",
                producer="brain-directory/1.0",
                source_revision="a" * 40,
            ),
        )

    def test_non_scalar_entity_type_is_structured_refusal(self) -> None:
        data = markdown().replace(b"type: Command", b'type: ["Command"]')
        self.assert_refusal(
            "UNKNOWN-ENTITY-TYPE",
            lambda: markdown_outcome(
                data=data,
                member="one.md",
                producer="brain-directory/1.0",
                source_revision="a" * 40,
            ),
        )

    def test_huge_markdown_number_is_structured_refusal(self) -> None:
        data = markdown().replace(
            b"namespace: test\n", b"namespace: test\nx_number: " + b"9" * 4301 + b"\n"
        )
        self.assert_refusal(
            "MALFORMED-FRONTMATTER",
            lambda: markdown_outcome(
                data=data,
                member="one.md",
                producer="brain-directory/1.0",
                source_revision="a" * 40,
            ),
        )

    def test_missing_required_field(self) -> None:
        data = b"---\ntype: Command\ndescription: bounded\n---\nDo it.\n"
        self.assert_refusal(
            "MISSING-REQUIRED-FIELD",
            lambda: markdown_outcome(
                data=data,
                member="one.md",
                producer="brain-directory/1.0",
                source_revision="a" * 40,
            ),
        )

    def test_credential_in_markdown(self) -> None:
        data = b"---\nid: one\ntype: Command\ndescription: bounded\napi_key: exposed\n---\nDo it.\n"
        self.assert_refusal(
            "CREDENTIAL-IN-EXPORT",
            lambda: markdown_outcome(
                data=data,
                member="one.md",
                producer="brain-directory/1.0",
                source_revision="a" * 40,
            ),
        )

    def test_nested_credential_mapping_in_markdown(self) -> None:
        data = markdown().replace(
            b"namespace: test\n",
            b"namespace: test\nx_provider:\n  api_key:\n    value: SYNTHETIC-NOT-A-SECRET\n",
        )
        self.assert_refusal(
            "CREDENTIAL-IN-EXPORT",
            lambda: markdown_outcome(
                data=data,
                member="one.md",
                producer="brain-directory/1.0",
                source_revision="a" * 40,
            ),
        )

    def test_nested_scalar_credential_in_markdown(self) -> None:
        data = markdown().replace(
            b"namespace: test\n",
            b"namespace: test\nx_provider:\n  api_key: SYNTHETIC-NOT-A-SECRET\n",
        )
        self.assert_refusal(
            "CREDENTIAL-IN-EXPORT",
            lambda: markdown_outcome(
                data=data,
                member="one.md",
                producer="brain-directory/1.0",
                source_revision="a" * 40,
            ),
        )

    def test_quoted_nested_credential_key_in_markdown(self) -> None:
        data = markdown().replace(
            b"namespace: test\n",
            b'namespace: test\nx_provider:\n  "api_key": SYNTHETIC-NOT-A-SECRET\n',
        )
        self.assert_refusal(
            "CREDENTIAL-IN-EXPORT",
            lambda: markdown_outcome(
                data=data,
                member="one.md",
                producer="brain-directory/1.0",
                source_revision="a" * 40,
            ),
        )

    def test_inline_nested_credential_key_in_markdown(self) -> None:
        data = markdown().replace(
            b"namespace: test\n",
            b'namespace: test\nx_provider: {"api_key":"SYNTHETIC-NOT-A-SECRET"}\n',
        )
        self.assert_refusal(
            "CREDENTIAL-IN-EXPORT",
            lambda: markdown_outcome(
                data=data,
                member="one.md",
                producer="brain-directory/1.0",
                source_revision="a" * 40,
            ),
        )

    def test_indented_inline_credential_key_in_markdown(self) -> None:
        data = markdown().replace(
            b"namespace: test\n",
            b'namespace: test\nx_provider:\n  settings: {"api_key":"SYNTHETIC-NOT-A-SECRET"}\n',
        )
        self.assertEqual(186, len(data))
        self.assertEqual(
            "2b0e3fb38665ea9289ab18e383563cc6435a8d2a13eb4e5662d879cd2d7a581b",
            digest(data),
        )
        self.assert_refusal(
            "CREDENTIAL-IN-EXPORT",
            lambda: markdown_outcome(
                data=data,
                member="one.md",
                producer="brain-directory/1.0",
                source_revision="a" * 40,
            ),
        )

    def test_duplicate_json_credential_key_is_structured_refusal(self) -> None:
        data = markdown().replace(
            b"namespace: test\n",
            b'namespace: test\nx_provider:\n  settings: {"api_key":"SYNTHETIC-NOT-A-SECRET","api_key":null}\n',
        )
        self.assertEqual(201, len(data))
        self.assertEqual(
            "c4f12b8d032c3e0d5318ed1a7e25089633ff0337993f7a1490e5b70f6880fa1e",
            digest(data),
        )
        refusal = self.assert_refusal(
            "MALFORMED-FRONTMATTER",
            lambda: markdown_outcome(
                data=data,
                member="one.md",
                producer="brain-directory/1.0",
                source_revision="a" * 40,
            ),
        )
        self.assertNotIn("SYNTHETIC-NOT-A-SECRET", str(refusal))

    def test_nested_duplicate_json_credential_key_is_structured_refusal(self) -> None:
        data = markdown().replace(
            b"namespace: test\n",
            b'namespace: test\nx_provider:\n  settings: {"nested":{"api_key":"SYNTHETIC-NOT-A-SECRET","api_key":null}}\n',
        )
        self.assertEqual(212, len(data))
        self.assertEqual(
            "3e6627a68ea9d11a0632eeeef180c4e1752fcdaa7292f67347aaf0351e05605d",
            digest(data),
        )
        refusal = self.assert_refusal(
            "MALFORMED-FRONTMATTER",
            lambda: markdown_outcome(
                data=data,
                member="one.md",
                producer="brain-directory/1.0",
                source_revision="a" * 40,
            ),
        )
        self.assertNotIn("SYNTHETIC-NOT-A-SECRET", str(refusal))

    def test_single_null_json_credential_key_is_permitted(self) -> None:
        data = markdown().replace(
            b"namespace: test\n",
            b'namespace: test\nx_provider:\n  settings: {"api_key":null}\n',
        )
        self.assertEqual(166, len(data))
        self.assertEqual(
            "e6a54a1f5671030bf733299c8cc57cdead7db342c7d2e151efcdb36576605601",
            digest(data),
        )
        outcome = markdown_outcome(
            data=data,
            member="one.md",
            producer="brain-directory/1.0",
            source_revision="a" * 40,
        )
        self.assertIn('{"api_key":null}', outcome.entity.extensions["raw_frontmatter"])

    def test_indented_simple_flow_credential_key_in_markdown(self) -> None:
        data = markdown().replace(
            b"namespace: test\n",
            b"namespace: test\nx_provider:\n  settings: {api_key: SYNTHETIC-NOT-A-SECRET}\n",
        )
        self.assertEqual(183, len(data))
        self.assertEqual(
            "84707cf64b7b384de6f1cbbff0052711490f3830f795c8acd05415546af39d57",
            digest(data),
        )
        self.assert_refusal(
            "CREDENTIAL-IN-EXPORT",
            lambda: markdown_outcome(
                data=data,
                member="one.md",
                producer="brain-directory/1.0",
                source_revision="a" * 40,
            ),
        )

    def test_indented_simple_flow_ordinary_metadata_is_retained(self) -> None:
        data = markdown().replace(
            b"namespace: test\n",
            b"namespace: test\nx_provider:\n  settings: {region: SYNTHETIC-REGION}\n",
        )
        self.assertEqual(176, len(data))
        self.assertEqual(
            "aab639aa6747d34215a9490334b28ee6364afc94a71b6099c6b96c52aca8f428",
            digest(data),
        )
        outcome = markdown_outcome(
            data=data,
            member="one.md",
            producer="brain-directory/1.0",
            source_revision="a" * 40,
        )
        self.assertIn("{region: SYNTHETIC-REGION}", outcome.entity.extensions["raw_frontmatter"])

    def test_multiple_simple_flow_ordinary_pairs_are_retained(self) -> None:
        data = markdown().replace(
            b"namespace: test\n",
            b"namespace: test\nx_provider:\n  settings: {region: SYNTHETIC-REGION, retry: 3}\n",
        )
        self.assertEqual(186, len(data))
        self.assertEqual(
            "9a89779529391367cef8b7d6bfae1b4088dfa0e39e64cfef3f32239f43895840",
            digest(data),
        )
        outcome = markdown_outcome(
            data=data,
            member="one.md",
            producer="brain-directory/1.0",
            source_revision="a" * 40,
        )
        self.assertIn(
            "{region: SYNTHETIC-REGION, retry: 3}",
            outcome.entity.extensions["raw_frontmatter"],
        )

    def test_unsupported_nested_flow_mapping_is_structured_refusal(self) -> None:
        data = markdown().replace(
            b"namespace: test\n",
            b"namespace: test\nx_provider:\n  settings: {nested: {region: SYNTHETIC-REGION}}\n",
        )
        self.assert_refusal(
            "MALFORMED-FRONTMATTER",
            lambda: markdown_outcome(
                data=data,
                member="one.md",
                producer="brain-directory/1.0",
                source_revision="a" * 40,
            ),
        )

    def test_malformed_nested_inline_collection_is_structured_refusal(self) -> None:
        data = markdown().replace(
            b"namespace: test\n",
            b"namespace: test\nx_provider:\n  settings: {region:\n",
        )
        self.assert_refusal(
            "MALFORMED-FRONTMATTER",
            lambda: markdown_outcome(
                data=data,
                member="one.md",
                producer="brain-directory/1.0",
                source_revision="a" * 40,
            ),
        )

    def test_over_limit_nested_inline_scalar_is_structured_refusal(self) -> None:
        data = markdown().replace(
            b"namespace: test\n",
            b"namespace: test\nx_provider:\n  settings: {count: " + b"9" * 4301 + b"}\n",
        )
        self.assert_refusal(
            "MALFORMED-FRONTMATTER",
            lambda: markdown_outcome(
                data=data,
                member="one.md",
                producer="brain-directory/1.0",
                source_revision="a" * 40,
            ),
        )

    def test_nested_unknown_markdown_metadata_is_retained(self) -> None:
        data = markdown().replace(
            b"namespace: test\n",
            b"namespace: test\nx_provider:\n  region: SYNTHETIC-REGION\n",
        )
        outcome = markdown_outcome(
            data=data,
            member="one.md",
            producer="brain-directory/1.0",
            source_revision="a" * 40,
        )
        self.assertIn("SYNTHETIC-REGION", outcome.entity.extensions["raw_frontmatter"])

    def test_nested_markdown_reference_is_retained(self) -> None:
        data = markdown().replace(
            b"namespace: test\n",
            b"namespace: test\nx_provider:\n  credential_ref: vault-item-one\n",
        )
        outcome = markdown_outcome(
            data=data,
            member="one.md",
            producer="brain-directory/1.0",
            source_revision="a" * 40,
        )
        self.assertIn("credential_ref: vault-item-one", outcome.entity.extensions["raw_frontmatter"])

    def test_flat_unknown_markdown_metadata_is_retained(self) -> None:
        data = markdown().replace(
            b"namespace: test\n", b"namespace: test\nx_provider: SYNTHETIC-REGION\n"
        )
        outcome = markdown_outcome(
            data=data,
            member="one.md",
            producer="brain-directory/1.0",
            source_revision="a" * 40,
        )
        self.assertEqual("SYNTHETIC-REGION", outcome.entity.extensions["x_provider"])

    def test_markdown_credential_reference_is_permitted(self) -> None:
        data = markdown().replace(
            b"namespace: test\n", b"namespace: test\ncredential_ref: vault-item-one\n"
        )
        outcome = markdown_outcome(
            data=data,
            member="one.md",
            producer="brain-directory/1.0",
            source_revision="a" * 40,
        )
        self.assertEqual("vault-item-one", outcome.entity.extensions["frontmatter"]["credential_ref"])

    def test_credential_in_workflow(self) -> None:
        data = json.dumps(
            {"nodes": [{"parameters": {"access_token": "exposed"}}], "connections": {}}
        ).encode()
        self.assert_refusal(
            "CREDENTIAL-IN-EXPORT", lambda: parse_workflow_json(data, "flow.json")
        )

    def test_ordinary_workflow_parameter_is_permitted(self) -> None:
        data = json.dumps(
            {"nodes": [{"parameters": {"region": "SYNTHETIC-REGION"}}], "connections": {}}
        ).encode()
        payload = parse_workflow_json(data, "flow.json")
        self.assertEqual("SYNTHETIC-REGION", payload["nodes"][0]["parameters"]["region"])

    def test_credential_key_in_workflow(self) -> None:
        data = json.dumps(
            {"nodes": [{"parameters": {"credential": "SYNTHETIC-NOT-A-SECRET"}}], "connections": {}}
        ).encode()
        self.assert_refusal(
            "CREDENTIAL-IN-EXPORT", lambda: parse_workflow_json(data, "flow.json")
        )

    def test_workflow_credential_reference_is_permitted(self) -> None:
        data = json.dumps(
            {"nodes": [{"parameters": {"credential_ref": "vault-item-one"}}], "connections": {}}
        ).encode()
        payload = parse_workflow_json(data, "flow.json")
        self.assertEqual("vault-item-one", payload["nodes"][0]["parameters"]["credential_ref"])

    def test_n8n_credential_reference_shape_is_permitted(self) -> None:
        data = json.dumps(
            {
                "nodes": [
                    {
                        "credentials": {
                            "sshPrivateKey": {
                                "id": "credential-record-one",
                                "name": "SSH Private Key account",
                            }
                        }
                    }
                ],
                "connections": {},
            }
        ).encode()
        payload = parse_workflow_json(data, "flow.json")
        reference = payload["nodes"][0]["credentials"]["sshPrivateKey"]
        self.assertEqual("credential-record-one", reference["id"])

    def test_n8n_credential_shape_with_extra_value_refuses(self) -> None:
        data = json.dumps(
            {
                "nodes": [
                    {
                        "credentials": {
                            "sshPrivateKey": {
                                "id": "credential-record-one",
                                "name": "SSH Private Key account",
                                "value": "SYNTHETIC-NOT-A-SECRET",
                            }
                        }
                    }
                ],
                "connections": {},
            }
        ).encode()
        self.assert_refusal(
            "CREDENTIAL-IN-EXPORT", lambda: parse_workflow_json(data, "flow.json")
        )

    def test_undeclared_loss(self) -> None:
        self.assert_refusal(
            "UNDECLARED-LOSS",
            lambda: require_declared_losses(
                member="one.md", omitted_fields={"provider_model"}, losses=()
            ),
        )

    def test_missing_declared_file_is_counted_as_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = envelope([{"path": "gone.md", "sha256": "0" * 64}])
            (root / "estate-export.json").write_text(json.dumps(payload), encoding="utf-8")
            result = import_directory(root)
        self.assertEqual(1, result.coverage.denominator)
        self.assertEqual("EXPORT-INCOMPLETE", result.outcomes[0].refusal.code)

    def test_missing_workflow_pair_is_counted_as_refused(self) -> None:
        workflow = json.dumps({"nodes": [{"id": "one"}], "connections": {}}).encode()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "flow.json").write_bytes(workflow)
            payload = envelope([{"path": "flow.json", "sha256": digest(workflow)}])
            (root / "estate-export.json").write_text(json.dumps(payload), encoding="utf-8")
            result = import_directory(root)
        self.assertEqual("MISSING-PAIR", result.outcomes[0].refusal.code)

    def test_duplicate_stable_id_is_counted_as_refused(self) -> None:
        one = markdown("same")
        two = markdown("same")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.md").write_bytes(one)
            (root / "two.md").write_bytes(two)
            payload = envelope(
                [
                    {"path": "one.md", "sha256": digest(one)},
                    {"path": "two.md", "sha256": digest(two)},
                ]
            )
            (root / "estate-export.json").write_text(json.dumps(payload), encoding="utf-8")
            result = import_directory(root)
        self.assertEqual(2, result.coverage.denominator)
        self.assertEqual("DUPLICATE-ID-CONFLICT", result.outcomes[1].refusal.code)

    def test_partial_export_retains_denominator_and_forces_review(self) -> None:
        source = markdown()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.md").write_bytes(source)
            payload = envelope(
                [{"path": "one.md", "sha256": digest(source)}],
                complete=False,
                missing=["agents/private.md"],
            )
            (root / "estate-export.json").write_text(json.dumps(payload), encoding="utf-8")
            result = import_directory(root)
        self.assertEqual(1, result.coverage.denominator)
        self.assertEqual("review_required", result.outcomes[0].disposition)
        self.assertEqual("PARTIAL-EXPORT", result.notices[0].code)

    def test_catalog_count_mismatch(self) -> None:
        components = (
            "component_type_id\tmember_count\tdisplay_name\twhat_it_is\n"
            "table\t2\tTable\tA database table\n"
        ).encode()
        members = "component_type_id\tmember\tdetail\ntable\tone\tdetail\n".encode()
        graphs = json.dumps({"denominator_graphs": 1, "graphs": [{"slug": "one"}]}).encode()
        self.assert_refusal(
            "COUNT-MISMATCH",
            lambda: parse_catalog(
                components_data=components,
                members_data=members,
                graphs_data=graphs,
                source_revision="a" * 40,
            ),
        )


class ContractControlTests(unittest.TestCase):
    def retained(self, *, authority: str = "unknown", disposition: str = "imported") -> Outcome:
        losses = (
            (
                Loss(
                    code="SOURCE-SEMANTIC-GAP",
                    field="tool",
                    disposition="review_required",
                    why="tool is unresolved",
                    source_value_present=False,
                ),
            )
            if disposition == "review_required"
            else ()
        )
        entity = PortableEntity(
            entity_id="agent.one",
            entity_type="Agent",
            name="One",
            summary="Does one bounded job",
            body="Do it.",
            source_ref={
                "producer": "brain-directory/1.0",
                "source_revision": "a" * 40,
                "member": "one.md",
                "sha256": "0" * 64,
            },
            activation="review_required" if losses else "disabled",
            authority_required=authority,
            losses=losses,
        )
        return Outcome(
            member="one.md",
            disposition=disposition,
            entity=entity,
            losses=losses,
        )

    def test_attention_unknown_authority_offers_review_not_enable(self) -> None:
        item = outcome_to_item(
            self.retained(),
            workspace="ws-test",
            actor_id="worker:estate-importer",
            created_at="2026-09-09T12:00:00Z",
            actor_github_provisioned=True,
            actor_postgres_provisioned=True,
        )
        self.assertEqual("review", item["kind"])
        self.assertEqual(["review-first", "keep-disabled"], [o["option_id"] for o in item["options"]])
        self.assertTrue(all("inverse" in option for option in item["options"]))

    def test_attention_complete_entity_is_decidable(self) -> None:
        item = outcome_to_item(
            self.retained(authority="none"),
            workspace="ws-test",
            actor_id="worker:estate-importer",
            created_at="2026-09-09T12:00:00Z",
            actor_github_provisioned=True,
            actor_postgres_provisioned=True,
        )
        self.assertEqual("approval", item["kind"])
        self.assertIn("enable", [option["option_id"] for option in item["options"]])

    def test_attention_actor_must_exist_in_github_and_postgres(self) -> None:
        with self.assertRaises(ImportRefusal) as caught:
            outcome_to_item(
                self.retained(),
                workspace="ws-test",
                actor_id="worker:estate-importer",
                created_at="2026-09-09T12:00:00Z",
                actor_github_provisioned=True,
                actor_postgres_provisioned=False,
            )
        self.assertEqual("ACTOR-UNKNOWN", caught.exception.code)

    def test_swarm_is_ordered_and_disabled(self) -> None:
        outcome = self.retained()
        coverage = Coverage.from_outcomes([outcome], control_fired=True)
        stages = plan_swarm([outcome], coverage)
        self.assertEqual("estate-integrity-reviewer", stages[0].agent)
        self.assertLess(
            [stage.agent for stage in stages].index("estate-agent-builder"),
            [stage.agent for stage in stages].index("estate-safety-reviewer"),
        )
        self.assertTrue(all(stage.activation == "disabled" for stage in stages))


if __name__ == "__main__":
    unittest.main()
