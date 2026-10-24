+++
date = '2026-10-24T14:40:00+02:00'
title = 'IntraBC in libaom: history of one loop'
tags = ['av1', 'libaom', 'intrabc', 'hevc', 'vvc', 'performance']
+++

## Where it started

In the [previous post]({{< ref "av1-intrabc.md" >}}) I made the IntraBC
hash table in libaom cheaper to build. After those changes I profiled
the build again to see what was left. One stage stood out: the fill, the
loop that puts every position of the frame into the table. At one thread
it took 5.1 to 6.4 ms per key frame on an Apple M3 Pro and 12.4 to 17.1
ms on an AMD Ryzen 9 5900X, more than any other stage of the build.

So I looked at the loop. By the time it runs, the hash of the block at
every pixel position is already computed and stored in a frame sized
buffer, row by row (`pos = y * pic_width + x`). The fill reads those hashes
and adds each position to one of 65,536 buckets. This is the loop, in
`av1_add_to_hash_map_by_row_with_precal_data()`
([hash_motion.c](https://aomedia.googlesource.com/aom/+/c8e0a150e482299f9bc4c4ae9cb1b308eb5c5df1/av1/encoder/hash_motion.c#328)),
shortened:

```c
while (step > 1) {
  for (int x_pos = x_offset; x_pos < x_end; x_pos += step) {
    for (int y_pos = y_offset; y_pos < y_end; y_pos += step) {
      const int pos = y_pos * pic_width + x_pos;
      // add pos to its bucket, unless the bucket already has 256
    }
  }
  // next offset, or half the step
}
```

The outer `while` visits the frame coarse to fine: first a grid with the
block size as the step, then the same grid shifted by half a step, then
half the step, down to 2. That part made sense to me, and I'll come back
to it. What surprised me was inside: `x` outside, `y` inside. The loop walks
down columns over a buffer that is stored in rows. At 2560 pixels wide
one row of hashes is 10 KB, so with a step of 8 rows every read lands
80 KB after the previous one. The function is even called "by row".

Walking a buffer against its memory layout is the classic slow loop. In
code this old I didn't believe it was a mistake. I assumed there was a
strong reason I didn't see yet, so I went looking for it.

## Step 1: libaom's history

The coarse to fine `while` came in 2025, but the column loop inside it is
older. It is already in the very first version of the file:
[libaom cc5d35d8dd](https://aomedia.googlesource.com/aom/+/cc5d35d8dd68841073edc724592d5a874bb724be/av1/encoder/hash_motion.c#305),
"hash based motion estimation for screen data", August 2017. The commit
message is that one line. No explanation of anything.

The names gave a hint. `x_end`, `src_is_added`, `add_value`, `crc_mask`
read like camelCase names converted to snake case. And a C function
called "add to hash map by row with precal data" sounded like a port of
something. HEVC has screen content coding with a hash search, so I
checked HM, the HEVC reference encoder.

## Step 2: the same loop in HEVC

HM has a branch for the screen content extensions, `HM-SCC-extensions`.
Its `TComHash.cpp` has a function with the same name. Here is the HM
version from June 2017 next to the libaom version from August 2017:

{{< cols >}}
{{< col >}}
**HM**, [TComHash.cpp at d4e2543131](https://vcgit.hhi.fraunhofer.de/jvet/HM/-/blob/d4e2543131698b5d3c1f6bb6214ee22b5301db38/source/Lib/TLibCommon/TComHash.cpp#L315-348)

```cpp
Void TComHash::addToHashMapByRowWithPrecalData(
    UInt* picHash[2], Bool* picIsSame,
    Int picWidth, Int picHeight,
    Int width, Int height)
{
  Int xEnd = picWidth - width + 1;
  Int yEnd = picHeight - height + 1;

  Bool* srcIsAdded = picIsSame;
  UInt* srcHash[2] = { picHash[0], picHash[1] };

  Int addValue = m_blockSizeToIndex[width][height];
  assert(addValue >= 0);
  addValue <<= m_CRCBits;
  Int crcMask = 1 << m_CRCBits;
  crcMask -= 1;

  for (Int xPos = 0; xPos < xEnd; xPos++)
  {
    for (Int yPos = 0; yPos < yEnd; yPos++)
    {
      Int pos = yPos*picWidth + xPos;
      //valid data
      if (srcIsAdded[pos])
      {
        BlockHash blockHash;
        blockHash.x = xPos;
        blockHash.y = yPos;

        UInt hashValue1 =
            (srcHash[0][pos] & crcMask) + addValue;
        blockHash.hashValue2 = srcHash[1][pos];

        addToTable(hashValue1, blockHash);
      }
    }
  }
}
```
{{< /col >}}
{{< col >}}
**libaom**, [hash_motion.c at cc5d35d8dd](https://aomedia.googlesource.com/aom/+/cc5d35d8dd68841073edc724592d5a874bb724be/av1/encoder/hash_motion.c#305)

```c
void av1_add_to_hash_map_by_row_with_precal_data(
    hash_table *p_hash_table, uint32_t *pic_hash[2],
    int8_t *pic_is_same, int pic_width, int pic_height,
    int block_size) {
  const int x_end = pic_width - block_size + 1;
  const int y_end = pic_height - block_size + 1;

  const int8_t *src_is_added = pic_is_same;
  const uint32_t *src_hash[2] = { pic_hash[0],
                                  pic_hash[1] };

  int add_value = hash_block_size_to_index(block_size);
  assert(add_value >= 0);
  add_value <<= crc_bits;
  const int crc_mask = (1 << crc_bits) - 1;

  for (int x_pos = 0; x_pos < x_end; x_pos++) {
    for (int y_pos = 0; y_pos < y_end; y_pos++) {
      const int pos = y_pos * pic_width + x_pos;
      // valid data
      if (src_is_added[pos]) {
        block_hash curr_block_hash;
        curr_block_hash.x = x_pos;
        curr_block_hash.y = y_pos;

        const uint32_t hash_value1 =
            (src_hash[0][pos] & crc_mask) + add_value;
        curr_block_hash.hash_value2 = src_hash[1][pos];

        hash_table_add_to_table(p_hash_table, hash_value1,
                                &curr_block_hash);
      }
    }
  }
}
```
{{< /col >}}
{{< /cols >}}

HM code: Copyright (c) 2010-2017, ITU/ISO/IEC, under the
[BSD License](https://vcgit.hhi.fraunhofer.de/jvet/HM/-/blob/f9f83a027b4bc33b60ebac4ed72a00d3fe2a6d93/COPYING#L1-31).

It is a line by line port, down to the `// valid data` comment. HM's
buckets are `std::vector<BlockHash>`, and libaom brought a C replacement
for it, `third_party/vector`, in the same commit.

So the loop came from HEVC. But this HM version has no reason for the
column order either. The hashes are already computed when it runs, it
only reads them. So the question moved into HM's history.

## Step 3: the reason, in 2014

The history of `TComHash.cpp` in HM starts with
[HM 351dc16154](https://vcgit.hhi.fraunhofer.de/jvet/HM/-/blob/351dc161543b83b7a734ad396f0070aef76ba725/source/Lib/TLibCommon/TComHash.cpp#L186-260),
"SCC: Added hash based inter search", by Bin Li, April 2014. In that
version the fill looked different. One function, `addToHashMapByRow()`,
computed the hashes and filled the table in the same loop.
Shortened:

```cpp
for (Int xPos = xStart; xPos < xEnd; xPos++)
{
  // hash the `width` pixels of every row at this x, once per row
  for (Int yPos = 0; yPos < picHeight; yPos++)
  {
    hashValue3Row[yPos] = TComHash::getCRCValue3(p, length);
    hashValue4Row[yPos] = TComHash::getCRCValue4(p, length);
  }

  // every block in this column is a CRC over `height` row hashes
  for (Int yPos = 0; yPos < yEnd; yPos++)
  {
    for (Int y = 0; y < height; y++)
    {
      toHash1[y] = hashValue3Row[yPos + y];
      toHash2[y] = hashValue4Row[yPos + y];
    }
    // CRC over toHash1 and toHash2, then addToTable()
  }
}
```

Here is the reason. A block hash was made in two steps. First each row of
the block, `width` pixels, was hashed into a row hash. Then the row
hashes of the block were hashed together. Take 4x4 blocks in column 0:

| Block | Row hashes it uses |
|---|---|
| (0, 0) | r0 r1 r2 r3 |
| (0, 1) | r1 r2 r3 r4 |
| (0, 2) | r2 r3 r4 r5 |

Blocks below each other in one column share all but one row. So the loop
hashed each row of the column once, kept the results in an array with
one value per row, and built every block of the column from that array.
Walking down columns saved CRC work.

A row by row walk can't do this cheaply. The block to the right, (1, 0),
covers columns 1 to 4, not 0 to 3, so the row hashes of column 0 are no
use for it. To reuse row hashes in row order you would have to keep them
for every x at once, a buffer as big as the frame. The column walk needed
one small array.

And in 2014 the order didn't matter for the result at all. Each bucket was
a `std::vector` with no limit, every position went in, and the search read
the whole bucket. The order only decided which of two equally good
candidates came first.

That's the only real reason the column order ever had.

## Step 4: the reason went away in 2016

Two years later
[HM 6ca4bba94c](https://vcgit.hhi.fraunhofer.de/jvet/HM/-/blob/6ca4bba94c4e72d5b3e4c2a706557d90627ca444/source/Lib/TLibCommon/TComHash.cpp#L221-317),
"Integration of JCTVC-W0078: bottom up hash value calculation", by Bin Li,
March 2016, changed how the hashes are made. Now they are computed for
the whole picture before the fill: the hash of every 2x2 block, then
every 4x4 block from four 2x2 hashes, then 8x8 from four 4x4 hashes, and
so on. The new hash functions walk the picture row by row, `for yPos`
outside and `for xPos` inside.

The fill became the separate, small function from step 2. It only reads
finished hashes, so there is nothing left to reuse down a column. But it
[kept the loop of the old function](https://vcgit.hhi.fraunhofer.de/jvet/HM/-/blob/6ca4bba94c4e72d5b3e4c2a706557d90627ca444/source/Lib/TLibCommon/TComHash.cpp#L319-352). Until May 2016 both versions lived in
the file behind a macro, then the old one was deleted
([HM 17ce486677](https://vcgit.hhi.fraunhofer.de/jvet/HM/-/blob/17ce486677d20e3de7b496bf7a238e0d6e97f9bf/source/Lib/TLibCommon/TComHash.cpp#L315-348),
where only the new function is left).

The buckets were still vectors with no limit, so the order still only
decided ties. Nobody had a reason to look at the loop again. This is the
version libaom ported in 2017.

## Step 5: 2025, libaom: the cap made the order matter

[libaom e91b7aa26d](https://aomedia.googlesource.com/aom/+/e91b7aa26d6d0979bba2bee5e1c27a7a695e0226/av1/encoder/hash_motion.c#288),
"intraBC: simplify hashing process", by Julio Barba, July 2025, limited
each bucket to 256 entries. That was a good change for speed: without a
limit, the bucket of a flat background collects positions from all over
the frame.

But from this point on the order matters. When a bucket is full, the
positions visited later are dropped, so the visit order decides which
256 positions survive. The same commit added the coarse to fine `while`
so that the first entries of a bucket spread over the whole frame. That
is the part that made sense to me in the beginning.

Inside it, the column loop from 2017 stayed. The commit message compares
size and time for the whole change. It doesn't compare a column walk with
a row walk. At the moment the order started to matter, nobody changed
the loop.

## What VVC did

VTM, the VVC reference encoder, still has the HM loop in its inter hash
search
([Hash.cpp](https://vcgit.hhi.fraunhofer.de/jvet/VVCSoftware_VTM/-/blob/051e5e7218c2d252e851d40c34c666113c131959/source/Lib/CommonLib/Hash.cpp#L324-359)).
But IntraBC in VVC got a new table written from scratch,
[`IbcHashMap::xxBuildPicHashMap()`](https://vcgit.hhi.fraunhofer.de/jvet/VVCSoftware_VTM/-/blob/051e5e7218c2d252e851d40c34c666113c131959/source/Lib/CommonLib/IbcHashMap.cpp#L182-228).
It walks the picture row by row, collects a (hash, position) pair for
every position and sorts the pairs by hash. When someone started fresh,
they walked rows.

## So why is the loop there?

Because of history. In 2014 the column walk was a good idea: it let one
small array of row hashes serve every block in a column. In 2016 the
hashing moved out of the loop and the reason disappeared, but the loop
stayed. In 2017 libaom copied it line by line. In 2025 the bucket limit
made the order matter, and the loop stayed again.

Nobody chose columns for the code we have today. The loop is the right
answer to a question from a 2014 function that no longer exists. I went
looking for a strong reason and found a fossil.

## Links

- HM, hash based inter search, 2014:
  [351dc16154](https://vcgit.hhi.fraunhofer.de/jvet/HM/-/blob/351dc161543b83b7a734ad396f0070aef76ba725/source/Lib/TLibCommon/TComHash.cpp#L186-260)
- HM, bottom up hash calculation (JCTVC-W0078), 2016:
  [6ca4bba94c](https://vcgit.hhi.fraunhofer.de/jvet/HM/-/blob/6ca4bba94c4e72d5b3e4c2a706557d90627ca444/source/Lib/TLibCommon/TComHash.cpp#L221-317)
- HM, after the old function was removed, 2016:
  [17ce486677](https://vcgit.hhi.fraunhofer.de/jvet/HM/-/blob/17ce486677d20e3de7b496bf7a238e0d6e97f9bf/source/Lib/TLibCommon/TComHash.cpp#L315-348)
- HM, the version libaom ported:
  [TComHash.cpp at d4e2543131](https://vcgit.hhi.fraunhofer.de/jvet/HM/-/blob/d4e2543131698b5d3c1f6bb6214ee22b5301db38/source/Lib/TLibCommon/TComHash.cpp#L315-348)
- libaom, the port, 2017:
  [cc5d35d8dd](https://aomedia.googlesource.com/aom/+/cc5d35d8dd68841073edc724592d5a874bb724be/av1/encoder/hash_motion.c#305)
- libaom, the bucket limit and the coarse to fine walk, 2025:
  [e91b7aa26d](https://aomedia.googlesource.com/aom/+/e91b7aa26d6d0979bba2bee5e1c27a7a695e0226/av1/encoder/hash_motion.c#288)
- VTM:
  [Hash.cpp](https://vcgit.hhi.fraunhofer.de/jvet/VVCSoftware_VTM/-/blob/051e5e7218c2d252e851d40c34c666113c131959/source/Lib/CommonLib/Hash.cpp#L324-359),
  [IbcHashMap.cpp](https://vcgit.hhi.fraunhofer.de/jvet/VVCSoftware_VTM/-/blob/051e5e7218c2d252e851d40c34c666113c131959/source/Lib/CommonLib/IbcHashMap.cpp#L182-228)
- My changes to the table:
  [218281](https://aomedia-review.googlesource.com/c/aom/+/218281/7/av1/encoder/hash_motion.c#328),
  [218282](https://aomedia-review.googlesource.com/c/aom/+/218282/8/av1/encoder/encodeframe.c#2079),
  [218381](https://aomedia-review.googlesource.com/c/aom/+/218381/7/av1/encoder/encodeframe.c#2064)
