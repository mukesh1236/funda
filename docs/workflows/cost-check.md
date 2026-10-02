# Cost check

The hosting bill is read from the **invoice line items**, not guessed from the code.
The last guess blamed CPU; the invoice disproved it.

## Read the invoice

| Line | What it measures |
|---|---|
| **Memory (per MB / min)** | Resident memory integrated over the cycle. This dominated: **$24.01 of $24.45** of usage on the July to August invoice |
| vCPU (per vCPU / min) | CPU time. **$0.18** |
| Network | Egress. $0.17 |
| Disk (per GB / min) | The volume. $0.09 |
| Hobby plan | $5.00, with **$5.00 of usage included** |

## Convert the memory line to something you can act on

```
average MB   = memory quantity / minutes in the cycle
average GB   = average MB / 1024
cost         ~ average GB x about $10 per GB-month
```

Worked example from that invoice: 103,718,965 MB-min over 31 days (44,640 min) is
about 2,323 MB, or **2.27 GB average**. At $10.24 per GB-month (a 30-day month) that is
$23.25, or **about $24.0 over these 31 days**, matching the $24.01 charged. That figure was far
above the 834 MB measured locally, which is what exposed the duplicate service and the
memory growth.

## What a healthy bill looks like

At about 0.39 GB resident: 0.39 x $10.24 = **$4.00** of memory, plus about $0.44 of
vCPU, network and disk = **$4.44 of usage, under the $5 credit**, so the invoice
approaches the $5 plan fee. Anything much above that means memory has grown; go to
[`memory-investigation.md`](memory-investigation.md).

## Checklist when the bill is high

1. Is there more than one service or project billing? (List them all.)
2. What is the memory line, converted to average GB, versus the current metric?
3. Is memory flat or climbing (floors across windows)?
4. Is the bill from before a fix? Billing is **in arrears**, and the cycle runs from the
   20th, so a fix mid-cycle shows up in the following invoice.

## What not to optimise

Moving cloud (AWS, Azure) saves roughly nothing for this architecture and costs days of
setup; see [ADR 0008](../adr/0008-stay-on-railway.md). The scheduler cadence changed
CPU by cents. Memory is the lever.
