"""The model marketplace: find a model on Hugging Face, see whether it fits, host it.

``facts``    what a model is, read from what the Hub says about it
``fit``      whether it runs on a cluster's accelerators, and how
``hardware`` what each cluster has, from what its machines report
``hub``      the Hugging Face Hub, cached, and honest about being unreachable
``curated``  a short list of models known to serve well, with their settings
``settings`` engine settings suggested for a model on a cluster
``host``     download (if needed) and deploy, in one step
"""
