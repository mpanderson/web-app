from .grants_gov import GrantsGovIngestor
#from .nih_guide import NihGuideIngestor
#from .nsf import NsfIngestor
#from .darpa import DarpaIngestor
#from .nih_search import NihGuideSearchIngestor 
from .dod_sbir import DodSbirIngestor
from .pcori import PcoriIngestor
from .rwjf import RwjfIngestor
from .gates import GatesIngestor

REGISTRY = {
    "grants_gov": GrantsGovIngestor,
    #"nih": NihGuideIngestor,
    #"nsf": NsfIngestor,
    #"darpa": DarpaIngestor,
    #"nih_full": NihGuideSearchIngestor,   # <-- NEW full crawler
    "pcori": PcoriIngestor,
    "rwjf": RwjfIngestor,
    "gates": GatesIngestor,
    "dod_sbir": DodSbirIngestor,
}
