# `genkei insider-clusters`

Detect insider buy/sell clusters (>=N reporters within K days).

## Options

```text
Usage: python -m genkei.cli insider-clusters [OPTIONS]                         
                                                                                
 Detect insider buy/sell clusters (≥N reporters within K days).                 
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --ticker         -t      TEXT                  Scope to one equity ticker.   │
│                                                Default: every issuer in the  │
│                                                lake.                         │
│ --sell                                         Look for sell clusters        │
│                                                instead of buy clusters (the  │
│                                                default).                     │
│ --min-reporters          INTEGER RANGE [x>=2]  Minimum distinct reporters    │
│                                                for a cluster.                │
│                                                [default: 2]                  │
│ --window-days            INTEGER RANGE [x>=1]  Maximum span (first to last   │
│                                                transaction) in days.         │
│                                                [default: 7]                  │
│ --since                  TEXT                  Start transaction_date        │
│                                                (YYYY-MM-DD).                 │
│ --until                  TEXT                  End transaction_date          │
│                                                (YYYY-MM-DD).                 │
│ --json                                         Emit machine-readable JSON.   │
│ --config                 PATH                  Watchlist path.               │
│                                                [default:                     │
│                                                /Users/michaeltookes/Desktop… │
│                                                Projects/genkei-capital-fund… │
│ --help                                         Show this message and exit.   │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Example

**Human output**

```console
$ genkei insider-clusters
Insider buy clusters (82 found, ≥2 reporters within 7d, watchlist)
------------------------------------------------------------------
  issuer   date_range                #         shares           $value  reporters
  TSM      2026-06-05               31          1,654         $125,721  Wei Che-Chia (Chairman and CEO), Mii Yuh-Jier (EVP and Co-COO), Chin Yung-Pei (EVP and Co-COO), Hou Yung-Chin (SVP and Deputy Co-COO), +27 more
  TSM      2026-05-08               28          1,610         $115,630  Wei Che-Chia (Chairman and CEO), Chin Yung-Pei (EVP and Co-COO), Mii Yuh-Jier (EVP and Co-COO), Hou Yung-Chin (SVP and Deputy Co-COO), +24 more
  TSM      2026-04-09               28          1,908         $110,416  Wei Che-Chia (Chairman and CEO), Chin Yung-Pei (EVP and Co-COO), Mii Yuh-Jier (EVP and Co-COO), Zhang Kevin Xiaoqiang (SVP and Deputy Co-COO), +24 more
  V        2008-03-25               20        586,609      $25,810,796  Al-Qadi Hani (dir), SHANAHAN WILLIAM S (dir), SAUNDERS JOSEPH W (Chairman and CEO), Morris John C. (President), +16 more
  MSFT     2004-09-14               12             46           $1,264  ALLCHIN JAMES E (Group Vice President), JOHNSON KEVIN R (Group Vice President), RUDDER ERIC D (Senior Vice President), COURTOIS JEAN PHILIPPE (Senior Vice President), +8 more
  0001131517 2004-12-30               10      1,502,040       $7,735,506  J P MORGAN PARTNERS GLOBAL INVESTORS A LP (10%), J P MORGAN PARTNERS GLOBAL INVESTORS CAYMAN II LP (10%), J P MORGAN PARTNERS GLOBAL INVESTORS CAYMAN LP (10%), J P MORGAN PARTNERS SBIC LLC (10%), +6 more
  SNOW     2020-09-18                9      3,780,042     $453,612,600  ICONIQ Strategic Partners III (10%), ICONIQ Strategic Partners IV GP (10%), ICONIQ Strategic Partners IV TT GP (10%), ICONIQ Strategic Partners IV (10%), +5 more
  MSFT     2015-01-28..2015-02-04    9      8,281,676     $340,817,625  Morfit G Mason (dir), VA Partners I (dir), ValueAct Capital Management (dir), ValueAct Capital Management (dir), +5 more
  0001297633 2004-12-31                9        776,898       $3,519,348  J P MORGAN PARTNERS GLOBAL INVESTORS A LP (10%), J P MORGAN PARTNERS GLOBAL INVESTORS CAYMAN II LP (10%), J P MORGAN PARTNERS GLOBAL INVESTORS CAYMAN LP (10%), JP MORGAN PARTNERS BHCA LP (10%), +5 more
  0001173657 2004-12-30                9      1,324,206      $10,514,196  J P MORGAN PARTNERS GLOBAL INVESTORS A LP (10%), J P MORGAN PARTNERS GLOBAL INVESTORS CAYMAN II LP (10%), J P MORGAN PARTNERS GLOBAL INVESTORS CAYMAN LP (10%), JP MORGAN PARTNERS BHCA LP (10%), +5 more
  CRM      2025-12-05                8        768,000     $200,125,440  Morfit G Mason (dir), VA Partners I (dir), ValueAct Capital Management (dir), ValueAct Capital Management (dir), +4 more
  CRM      2024-06-03                8      3,424,000     $798,374,080  Morfit G Mason (dir), VA Partners I (dir), ValueAct Capital Management (dir), ValueAct Capital Management (dir), +4 more
  MSFT     2014-05-08..2014-05-09    8     23,604,008     $933,610,476  Morfit G Mason (dir), VA Partners I (dir), ValueAct Capital Management (dir), ValueAct Capital Management (dir), +4 more
  MSTR     2025-07-29                7        242,855      $21,856,950  Briger Peter L JR (dir), Patten Jarrod M (dir), Le Phong (President & CEO), Montgomery Jeanine (VP & CAO), +3 more
  0001894562 2022-10-24                7      5,600,000      $95,200,000  Alphabet Inc., GV 2019 GP (10%), GV 2019 GP (10%), GV 2019 (10%), +3 more
... (67 more lines)
```

**JSON (`--json`)**

```console
$ genkei insider-clusters --json
[
  {
    "issuer_cik": "0001046179",
    "issuer_ticker": "TSM",
    "direction": "buy",
    "window_start": "2026-06-05",
    "window_end": "2026-06-05",
    "span_days": 0,
    "reporter_count": 31,
    "total_shares": "1654",
    "total_value_usd": "125720.54",
    "reporters": [
      {
        "reporter_cik": "0002113717",
... (5074 more lines)
```

## See also

[`insiders`](insiders.md) · [`signals`](signals.md)

---

_Page generated for B-047. Example output is a point-in-time capture; shape is stable, values are not. Regenerate when the command's flags change._
