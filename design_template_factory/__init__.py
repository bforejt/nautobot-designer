"""nautobot-designer.

Pure-Python core of the capture-to-design pipeline
(docs/capture-to-design-plan.md):

    capture (Nautobot job, see jobs/) -> site-spec JSON
    templatize (dtf propose-params)   -> parameter map, human-reviewed
    render (dtf render)               -> Design Builder design package

Nothing in this package imports Nautobot; the ORM boundary lives entirely in
the jobs/ package so the templatize and render stages run anywhere.
"""

__version__ = "0.1.0"
