from llm_security_digest.papers.models import PaperFacts
from llm_security_digest.papers.pipeline import validate_bibtex


def _paper(*authors: str) -> PaperFacts:
    return PaperFacts(
        paper_id="acl:2024.emnlp-main.10",
        source="acl",
        source_id="2024.emnlp-main.10",
        title="Hateful Word in Context Classification",
        authors=list(authors),
        abstract="Abstract",
        publication_status="published",
        venue="EMNLP",
        published_at="2024-01-01",
        updated_at=None,
        doi=None,
        landing_url="https://aclanthology.org/2024.emnlp-main.10/",
        pdf_url="https://aclanthology.org/2024.emnlp-main.10.pdf",
    )


def test_bibtex_latex_letter_macros_match_unicode_author_names() -> None:
    paper = _paper("Hoeken, Sanne", "Zarrieß, Sina", "Alacam, Özge")
    bibtex = r'''@inproceedings{hoeken-etal-2024-hateful,
      title = "Hateful Word in Context Classification",
      author = {Hoeken, Sanne and Zarrie{\ss}, Sina and Alacam, {\"O}zge}
    }'''

    validate_bibtex(paper, bibtex)


def test_bibtex_author_identity_still_rejects_different_author() -> None:
    paper = _paper("Hoeken, Sanne", "Zarrieß, Sina", "Alacam, Özge")
    bibtex = r'''@inproceedings{wrong,
      title = "Hateful Word in Context Classification",
      author = {Hoeken, Sanne and Zarrie{\ss}, Sina and Alacam, {\"O}zan}
    }'''

    try:
        validate_bibtex(paper, bibtex)
    except ValueError as exc:
        assert str(exc) == "BibTeX authors do not match authoritative metadata"
    else:
        raise AssertionError("different author must not pass BibTeX identity validation")
