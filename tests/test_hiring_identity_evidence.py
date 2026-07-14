import unittest

from job_source_agent.contracts import PipelineContext
from job_source_agent.models import CompanyInput
from job_source_agent.stages.upstream import HiringIdentityResolutionStage


class _Identity:
    def __init__(self, name, **kwargs):
        self.hiring_entity_name = name
        self.career_root_url = kwargs.get("career_root_url")
        self.official_website_url = kwargs.get("official_website_url")
        self.relationship_type = kwargs.get("relationship_type")
        self.relationship_verified = kwargs.get("relationship_verified")
        self.verification_method = kwargs.get("verification_method")
        self.evidence_url = kwargs.get("evidence_url")


class _Resolver:
    def __init__(self, identity):
        self.identity = identity

    def resolve(self, *args):
        return self.identity, {}


class HiringIdentityEvidenceTests(unittest.TestCase):
    def test_same_entity_is_explicitly_verified_without_resolver_output(self):
        context = PipelineContext.from_company(
            CompanyInput(company_name="Acme", company_website_url="https://acme.example")
        )

        execution = HiringIdentityResolutionStage(_Resolver(None)).run(context)

        evidence = execution.updates["hiring_identity_evidence"]
        self.assertEqual(evidence.relationship_type, "same_entity")
        self.assertTrue(evidence.verified)
        self.assertEqual(evidence.hiring_entity_name, "Acme")

    def test_different_entity_requires_structured_verified_relationship(self):
        context = PipelineContext.from_company(
            CompanyInput(company_name="Child Brand", company_website_url="https://child.example")
        )
        identity = _Identity("Parent Corp")

        execution = HiringIdentityResolutionStage(_Resolver(identity)).run(context)

        evidence = execution.updates["hiring_identity_evidence"]
        self.assertEqual(evidence.relationship_type, "alternate_employer")
        self.assertFalse(evidence.verified)

    def test_explicit_parent_relationship_remains_verified(self):
        context = PipelineContext.from_company(
            CompanyInput(company_name="Child Brand", company_website_url="https://child.example")
        )
        identity = _Identity(
            "Parent Corp",
            career_root_url="https://jobs.parent.example",
            relationship_type="brand_parent",
            relationship_verified=True,
            verification_method="identity_rule",
            evidence_url="https://jobs.parent.example",
        )

        execution = HiringIdentityResolutionStage(_Resolver(identity)).run(context)

        evidence = execution.updates["hiring_identity_evidence"]
        self.assertEqual(evidence.relationship_type, "brand_parent")
        self.assertTrue(evidence.verified)
        self.assertEqual(evidence.hiring_entity_name, "Parent Corp")


if __name__ == "__main__":
    unittest.main()
