"""Face enrollment: detect faces, cluster them online, let the user name each person once.

The 'who' axis the story layer said was missing. insightface gives detection + a 512-d
identity embedding; clustering + naming turn anonymous faces into named people, reused
library-wide. Cluster logic is pure/testable; detection wraps insightface (validated live).
"""
