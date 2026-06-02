# Vision

## Problem

When coding, real-time feedback from a knowledgeable observer accelerates
learning and catches mistakes early. But having a human pair is expensive and
not always available.

## Solution

geno-pear is a persistent file-monitoring companion that watches a code file
for changes and writes contextual feedback to a sidecar markdown file. The
user codes in their editor; geno-pear observes each save and updates a
scratchpad they can glance at.

Different personas shape the feedback: a reviewer catches bugs, a mentor
discusses design, a pair programmer thinks alongside you.

## Success Criteria

- User saves a file and sees updated feedback in scratchpad.md within seconds
- Feedback is compact enough for an IDE sidebar (~60 char width)
- Bidirectional communication via AI channel markers in the code file
- Session history persisted for later review
- Other skillsets can inject custom personas via the `custom` persona type
