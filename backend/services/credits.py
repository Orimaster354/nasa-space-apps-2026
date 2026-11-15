"""Data credits and acknowledgments shown in the app (P1-18).

The wording of the SPHEREx and IRSA acknowledgments is copied verbatim from IRSA's own pages, read on 2026-11-15:
  * https://irsa.ipac.caltech.edu/Missions/spherex.html  ("Please include the following acknowledgement in any published material that makes use of SPHEREx data products")
  * https://irsa.ipac.caltech.edu/ack.html
The dataset DOIs are from https://www.ipac.caltech.edu/dois/irsa. The SIMBAD wording and reference are from the SIMBAD user's guide (confirmed by
search, 2026-11-15); the Astropy wording from https://www.astropy.org/acknowledging.html. Do not paraphrase the acknowledgment sentences.
"""
from __future__ import annotations

RETRIEVED_ON = "2026-11-15"

CREDITS: dict = {
    "retrievedOn": RETRIEVED_ON,
    "acknowledgments": [
        {"id": "spherex", "label": "SPHEREx",
         "text": ("This publication makes use of data products from the Spectro-Photometer for the History of the Universe, Epoch of Reionization and "
                  "Ices Explorer (SPHEREx), which is a joint project of the Jet Propulsion Laboratory and the California Institute of Technology, and "
                  "is funded by the National Aeronautics and Space Administration."),
         "source": "https://irsa.ipac.caltech.edu/Missions/spherex.html"},
        {"id": "irsa", "label": "NASA/IPAC Infrared Science Archive",
         "text": ("This research has made use of the NASA/IPAC Infrared Science Archive, which is funded by the National Aeronautics and Space "
                  "Administration and operated by the California Institute of Technology."),
         "source": "https://irsa.ipac.caltech.edu/ack.html"},
        {"id": "simbad", "label": "SIMBAD / CDS (names and proper motions)",
         "text": "This research has made use of the SIMBAD database, operated at CDS, Strasbourg, France.",
         "source": "https://simbad.cds.unistra.fr/guide/", "reference": "Wenger et al. 2000, A&AS 143, 9 (The SIMBAD astronomical database)"},
        {"id": "astropy", "label": "Astropy (image and coordinate handling)",
         "text": ("This research made use of Astropy, a community-developed core Python package and an ecosystem of tools and resources for astronomy "
                  "(Astropy Collaboration 2013, 2018, 2022)."),
         "source": "https://www.astropy.org/acknowledging.html"},
    ],
    "datasets": [
        {"name": "SPHEREx Data Release 1 (DR1) Spectral Images", "doi": "10.26131/IRSA678", "url": "https://doi.org/10.26131/IRSA678", "author": "SPHEREx Team"},
        {"name": "SPHEREx Quick Release Spectral Images, QR3", "doi": "10.26131/IRSA662", "url": "https://doi.org/10.26131/IRSA662", "author": "SPHEREx Team"},
    ],
    "papers": [
        {"label": "Mission: Bock et al. (2026)", "url": "https://ui.adsabs.harvard.edu/abs/2026ApJ...999..139B/abstract"},
        {"label": "Pipeline: Akeson et al. (2026)", "url": "https://ui.adsabs.harvard.edu/abs/2026ApJS..285...67A/abstract"},
        {"label": "Sky simulator: Crill et al. (2025)", "url": "https://ui.adsabs.harvard.edu/abs/2025ApJS..281...10C/abstract"},
        {"label": "Instrument and calibration: Korngut et al. (2026)", "url": "https://ui.adsabs.harvard.edu/abs/2026arXiv260329835K/abstract"},
    ],
    "resources": [
        {"label": "WiseView (Caselden et al. 2018, ASCL 1806.004)", "url": "https://github.com/backyardworlds/wiseview",
         "note": "A user-interface reference for blinking multi-epoch images. It uses WISE data, not SPHEREx. No WiseView code is used in this app."},
        {"label": "NASA Space Apps Challenge 2026: Planet X and SPHEREx", "url": "https://www.spaceappschallenge.org/"},
    ],
    "aiNote": ("Navi's written explanations are produced by Google Gemini language models from measurements this app computed. The AI does not "
               "measure anything itself, and its explanations are not scientific findings."),
    "dataNote": ("Images and measurements come from the public SPHEREx archive at NASA IRSA and are processed by this app: resampled to a common grid, "
                 "background-matched and measured. Processed pictures and stacks are derived products, not NASA observations."),
}
