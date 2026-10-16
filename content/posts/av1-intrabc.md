+++
date = '2026-10-14T11:45:20+02:00'
title = 'Faster IntraBC in libaom'
tags = ['av1', 'libaom', 'intrabc', 'performance']
+++

## Summary

IntraBC (intra block copy) reduces AV1 key frame size on screen content by
10 to 21% in our tests, but in libaom's real time mode it increased key
frame CPU time by 25 to 41% on an Apple M3 Pro. Profiling shows that
almost all of the extra time goes into building a per-frame hash table,
not into the search that uses it.

This post describes two changes to that table:

1. A flat data layout. We replace one growable vector per bucket with a
   single array per block size, built in three passes: count, prefix sum,
   fill. The output is bit exact. Key frame CPU time at one thread drops
   by 11 to 17% on an Apple M3 Pro and by 17 to 21% on an AMD Ryzen 9
   5900X.
2. One table per tile, built in parallel. The encoder threads build the
   tables before tile encoding starts, instead of the main thread building
   one table for the whole frame. With four threads this takes another 22
   to 32% off the key frame time.

Together they cut key frame latency at four threads by 44 to 50% on the
M3 Pro and by 51 to 55% on the Ryzen.

Both patches are in review on libaom's Gerrit:

- [Store the IntraBC hash table in flat arrays](https://aomedia-review.googlesource.com/c/aom/+/218281)
- [Build one IntraBC hash table per tile in parallel](https://aomedia-review.googlesource.com/c/aom/+/218282)

## Background

### IntraBC

IntraBC lets a block predict from an already reconstructed region of the
same frame. The block signals a displacement vector, which works like a
motion vector that points inside the current frame. Screen content is a
good fit. The same glyphs and UI elements show up all over the frame, and
an exact copy is often cheaper than any intra predictor.

### Hash based candidate search

An exhaustive search for copy candidates is too slow for real time
encoding, so libaom uses a hash table. Before it encodes a key frame, the
encoder:

1. Computes a CRC32C hash of the 8x8 block at every pixel position, not
   only at block aligned positions.
2. Inserts every position into a hash table with 65,536 buckets, indexed
   by the low 16 bits of the hash. Each entry stores `x`, `y` and the full
   32 bit hash.
3. Limits each bucket to 256 entries. Inserts into a full bucket are
   dropped.

During mode search, an 8x8 block computes its own hash, reads the matching
bucket and checks the candidates for an exact match.

In real time screen mode only the 8x8 table is built, at every speed.
Good quality mode builds tables for more block sizes.

## Problem

We encoded key frames from five screen recordings (see Test setup) in
real time mode at speed 11, with IntraBC on and off. On an M3 Pro,
IntraBC added 25 to 41% CPU time per key frame on a single thread (30 to
47 ms). With four threads, a key frame took 2.0 to 2.5 times as long as
with IntraBC off.

The rest of this section comes from the earlier run (see Test setup). In
that run IntraBC made key frames 10 to 21% smaller at one thread and 7 to
17% smaller at four threads, where tiles limit what a block can copy.

A sampling profile of the difference between IntraBC on and off put 95 to 97%
of the extra samples in the frame level hash table build in
`encode_frame_internal()`. The breakdown for one clip (IDE and video,
one thread):

| Component | Extra samples |
|---|---|
| Table insert (`aom_vector_push_back()`, `malloc`) | 485 |
| CRC32C hashing of every position | 210 |
| Table teardown (`av1_hash_table_destroy()`) | 109 |

The search itself, `av1_intrabc_hash_search()`, took 1 to 6% of encode
time.

Instrumentation showed a large mismatch between the work spent on the
table and how much of it gets used. Per key frame:

- About 3.66 million insert attempts, one per pixel position.
- 48 to 67% of those inserts hit a bucket that is already full. Flat
  regions produce the same hash at many positions, so a few hundred
  buckets fill almost at once.
- 7,500 to 12,500 lookups, and 1,400 to 4,000 blocks that choose IntraBC.

The table is written millions of times and read thousands of times, so
the cost of the build sets the cost of the feature.

## Test setup

We compare libaom main (71ccfafa26), main with the flat table, and main
with the flat table and the per tile build, on two machines:

| Machine | OS | Compiler | Build |
|---|---|---|---|
| Apple M3 Pro | macOS | Apple clang 21.0.0 | real time only, `-O3 -DNDEBUG` |
| AMD Ryzen 9 5900X | Windows 11 | MSVC 19.44 | real time only, `/O2 /Ob2 /DNDEBUG` |

The input is five desktop recordings, captured losslessly and converted
to 8 bit 4:2:0. We take 20 frames from each recording, evenly spaced or
every 15th frame of a 300 frame excerpt, and encode each one as the first
frame of a new encoder, so every frame is a key
frame. Four recordings are 2560x1440 macOS desktops, and one is a
2559x1372 Windows desktop. The clip names in the charts say what is on
screen.

Encoder settings:

- Real time usage, speed 11 (`--cpu-used=11`), one pass, no lag.
- Screen content tuning, IntraBC on, palette on, 64x64 superblocks.
- VBR with the quantizer held between 8 and 13.
- One thread with a single tile, two threads with 2x1 tiles, or four
  threads with row multithreading and 2x2 tiles.

On each machine the one and four thread runs were one interleaved run,
and the two thread runs a second one. Each had five rounds, with the order
of the builds flipped each round. Times are medians. A change between two
builds is the median of the per round changes. At one thread the CPU time
and the wall time are the same, so one thread results show CPU time, and
two and four thread results show wall time.

The two machines differ in CPU, instruction set, compiler, OS and page
size (16 KB on macOS, 4 KB on Windows). A gap between them can't be pinned
on any one of these.

Some profiles and counts come from an earlier run on the M3 Pro, on an
older libaom base, with the same clips and settings. We call it the
earlier run.

## A flat hash table

Gerrit change: [218281](https://aomedia-review.googlesource.com/c/aom/+/218281).

### Previous design

Each bucket was a `Vector` from `third_party/vector`:

```c
typedef struct _hash_table {
  // a dynamically allocated array of kMaxAddr elements
  Vector **p_lookup_table;
} hash_table;
```

The first insert into a bucket allocated the `Vector` and its data, with
an initial capacity of 10. Later inserts called `aom_vector_push_back()`,
which reallocates as the vector grows, five times on the way to 256
entries. A size check through `aom_vector_byte_size()` enforced the 256
entry cap. After each key frame the encoder freed every bucket. So every
key frame paid for two allocations per used bucket, the regrowth, and the
matching frees.

### Data layout

The entries of each block size now live in one contiguous array, with
each bucket's entries stored back to back. Two arrays index into it:

```c
typedef struct _hash_table {
  uint16_t *bucket_count;  // entries in each bucket
  uint32_t *bucket_start;  // first entry of each bucket
  block_hash *block_hashes[6];  // one array per block size
  uint32_t block_hashes_size[6];
} hash_table;
```

### Why counting sort

Building the table is really a sort. Every position has a key, its bucket
index, and the table is just the positions grouped by key. The key space is
small and fixed at 65,536 buckets per block size. That makes it a good fit
for counting sort: count how many items each key has, turn the counts into
offsets with a prefix sum, then place every item straight into its slot.
There are no comparisons, and the work is linear in the number of
positions. The result is the same layout as compressed sparse
row (CSR) storage for sparse matrices.

The 256 entry cap fits this approach well, for two reasons:

- A bucket keeps `min(total, 256)` entries, whatever order the inserts
  come in. So the count pass can give the exact final size of every bucket
  before anything is stored.
- No count goes above 256, so `bucket_count` fits in a `uint16_t`. The
  index for one block size is 384 KB (65,536 x 2 bytes of counts plus
  65,536 x 4 bytes of offsets). That fits easily in the M3's L2, and is
tight in Zen 3's 512 KB L2.

Two simpler layouts don't work as well here:

- A fixed array of 256 slots per bucket needs no count pass, but it costs
  65,536 x 256 x 8 bytes = 128 MiB per block size, and most of it would
  sit empty. A 1440p key frame stores 1.3 to 1.9 million entries, which
  fill about 8 to 12% of those slots.
- One shared array with a linked list per bucket also skips the count
  pass. But a bucket's entries end up spread across the array, so the
  search follows a pointer for every candidate, and every entry needs
  room for a next pointer. Keeping the old order also needs a tail pointer
  per bucket, because inserting at the head would reverse the order and
  change which candidates the search sees first.

With counting sort, a bucket's entries are contiguous, so the search reads
them in one linear scan. The cost is one extra pass over the hash plane,
and once the count pass counts runs (see below), that pass takes 1.0 to
1.3 ms per 1440p frame in a microbenchmark.

Each block size gets its own array. With one array for all sizes, the
non real time modes would have to grow it partway through a frame and
copy the sizes built so far. Separate arrays never need that copy.

### Three pass build

Each table is built in three passes over the precomputed hash plane:

1. Count. Walk the frame in raster order and count the entries each
   bucket receives, capped at 256.
2. Prefix sum. Convert the counts into start offsets. The total entry
   count is now known, so the array is sized with one allocation.
3. Fill. Run the existing hierarchical insert loop and write each
   entry directly into its slot.

The inner loop of the fill pass:

```c
const int bucket = pic_hash[pos] & crc_mask;
const int count = bucket_count[bucket];
if (count >= kMaxCandidatesPerHashBucket) continue;

block_hash *const curr_block_hash =
    &block_hashes[bucket_start[bucket] + count];
curr_block_hash->x = x_pos;
curr_block_hash->y = y_pos;
curr_block_hash->hash_value2 = pic_hash[pos];
bucket_count[bucket] = count + 1;
```

The build no longer allocates or frees anything per bucket, and an insert
into a full bucket costs one load and one compare.

The read side changes little. `av1_hash_get_first_iterator()` becomes:

```c
const block_hash *av1_hash_get_first_block_hash(const hash_table *p_hash_table,
                                                uint32_t hash_value);
```

`av1_hash_table_count()` keeps its meaning, so the search walks a pointer
and a count instead of a vector iterator.

### Run length counting

In a microbenchmark on hash planes dumped from the test clips, a direct
count pass, one `bucket_count[b]++` per position, took 4 to 6 ms per 1440p
frame. In flat regions thousands of consecutive positions map to
the same bucket. Each increment then depends on the store of the previous
one to the same address, and the loop runs at the latency of that memory
dependency instead of its throughput.

The fix is simple. Count runs of equal buckets within a row and add each
run once:

```c
for (int x_pos = 0; x_pos < x_end; x_pos++) {
  const int curr_bucket = row_hash[x_pos] & crc_mask;
  if (curr_bucket != bucket) {
    add_to_bucket_count(&bucket_count[bucket], run);
    bucket = curr_bucket;
    run = 0;
  }
  run++;
}
```

This brings the count pass down to 1.0 to 1.3 ms per frame, about 4x
faster. Inside the full encoder the stage timers later in this post show
1.8 to 2.1 ms on the M3 Pro.

### Buffer lifetime

The arrays persist across frames and grow only when a frame needs more
room. `av1_hash_table_create()` now only clears the counts, the per-frame
teardown in `encode_frame_internal()` is gone, and the buffers are freed
with the encoder. Worker threads get a shallow copy of the table after it
is built and only read it.

### Correctness

The change must not alter the bitstream. Three properties guarantee that:

1. The number of entries a bucket keeps is `min(total, 256)`, which does
   not depend on insertion order. So a count pass in raster order gives
   every bucket a slot range of exactly the right size.
2. The count pass and the fill visit the same set of positions, each one
   once.
3. The fill visits positions in the same order as the old insert loop and
   stops at the same cap, so each bucket ends up with the same entries in
   the same order.

We didn't rely on the argument alone:

- A checksum of every bucket matched the old table on 15 frames dumped
  from all five clips.
- Packet hashes matched the baseline in 28 configurations: real time at
  one and four threads, IntraBC off, a reused encoder, frame size changes,
  good quality speed 6, and all intra speeds 3 and 6.
- In the benchmark runs, the output on the M3 Pro and on the Ryzen matched
  byte for byte, with and without the patch.
- The libaom unit tests showed no new failures in the real time only and
  default builds with assertions on. AddressSanitizer and `leaks` reported
  no errors.

### Memory

The arrays now outlive the frame, so the encoder keeps memory that the
old code gave back after every key frame.

| Layout | 8x8 table, real time, 1440p |
|---|---|
| Old layout (estimated, freed after each frame) | 19 to 26 MB per key frame |
| New layout, on the test clips (retained) | 12 to 17 MB |
| New layout, worst case | about 29 MB |

Peak RSS still drops with the flat layout. A real time 1440p encode went
from 157 to 123 MB. In good quality mode at cpu-used 3, where all block
sizes are built, the retained tables come to about 148 MB.

The speedup doesn't come from keeping the buffers. A variant that frees
the arrays after every frame measured the same encode time. We keep them
because real time streams have frequent key frames. For encodes with rare
key frames, freeing them per frame makes more sense, and it costs 0.2 to
1.2 ms per key frame.

### Speed

At one thread the flat table cuts key frame CPU time by 11 to 17% on the
M3 Pro and by 17 to 21% on the Ryzen. At four threads it cuts the wall
time by 25 to 32% and 31 to 35%. Hover over a clip in the charts for the
exact times.

At one and four threads the patch was faster in every round on every
clip on both machines.

{{< svgchart "charts/intrabc-cpu-1t.svg" "CPU time per key frame at one thread. Both machines share one axis. The label is the change against main." >}}

{{< svgchart "charts/intrabc-wall-4t.svg" "Wall time per key frame at four threads. Both machines share one axis. The label is the change against main." >}}

At one thread the Ryzen is about 1.8 to 1.9 times slower than the M3 Pro
on this workload, so it gains more in absolute terms. At four threads the
patch saves 20 to 31 ms per key frame on the M3 Pro and 50 to 69 ms on
the Ryzen.

The earlier run gives a closer look. In an isolated benchmark, inserting
into the table and freeing it took 12.2 to 37.5 ms per frame before and
4.5 to 8.5 ms after. Relative to IntraBC off, the extra single thread CPU
cost of IntraBC was 28 to 49% before and 12 to 20% after. At four threads
a key frame took 2.1 to 2.5 times as long as with IntraBC off before, and
1.5 to 1.8 times after. Encodes with IntraBC off ran at the
same speed.

The four thread gain is larger because the table build runs serially on
the main thread before tile encoding starts. Every millisecond cut from the
build comes straight off the critical path.

### What is left

In the profile from the earlier run, table inserts and teardown
fell from 20.2% of encoder samples to 5.6%. CRC32C hashing is now the
larger cost at 8.1%, about 12 ms per key frame.

{{< svgchart "charts/intrabc-build-cost.svg" "Hash table build cost per key frame for the IDE and video clip, M3 Pro, one thread, earlier run. Estimated from profile sample shares and the measured key frame CPU time." >}}

The whole hash build, CRC passes included, is now about 21 ms per key
frame, and all of it still runs on
the main thread before any tile starts. At four threads that serial part
is about a third of the key frame. The next change goes after it.

## One hash table per tile, built in parallel

Gerrit change: [218282](https://aomedia-review.googlesource.com/c/aom/+/218282). It applies on top of the flat table.

### Two problems with one frame wide table

The first problem is the serial build. In a four thread profile of the
IDE and video clip from the earlier run, 34.6% of the main thread's
samples came before tile encoding started, and almost all of that was the hash table build. The
other three threads sat idle during that time.

The second problem is less obvious. A block can only copy from its own
tile, but every tile searched the same frame wide table. With 2x2 tiles,
50 to 67% of the entries in a bucket pointed into another tile. The
search checked those entries and then threw them away. At speed 11 the
search only looks at the first 64 entries of a bucket, so a tile could
in theory miss every copy it was allowed to use.

### Design

Each tile now gets its own table, with only the blocks that lie entirely
inside the tile:

- The tables live in an array indexed by `tile_row * tile_cols +
  tile_col`. Positions stay in frame coordinates, so the search code
  doesn't change apart from picking the table of `xd->tile`.
- The 2x2 pass, the CRC passes and the insert all work on the tile's
  area. The fill keeps the same hierarchical order, starting at the
  tile's corner. All tiles share the two frame sized hash buffers, and
  each one writes only its own area.
- The encoder threads build the tables before tile encoding starts. Each
  worker takes tiles `t`, `t + n`, `t + 2n` and so on, and all workers
  sync before the first tile row is encoded.
- With one tile there is one table for the whole frame, the same as
  before. A frame with more than 16 tiles falls back to one frame wide table,
  which bounds the memory. That table is built on the main thread as
  before, so those frames keep both problems above.
- Bucket arrays are now allocated only for the block sizes that get
  built. At speed 11 that is just 8x8, so a table needs 384 KB of buckets
  instead of 2.36 MB.

The worker loop is short:

```c
for (int t = thread_data->start; t < num_tables; t += num_workers) {
  if (!av1_build_intrabc_hash_table(cpi, t)) {
    aom_internal_error(error_info, AOM_CODEC_MEM_ERROR,
                       "Error adding data to intrabc_hash_table");
  }
}
```

A worker that runs out of memory reports it through its own error info,
the same way the tile encoding workers do.

In the same four thread profile, the serial share of the main thread fell
from 34.6% to 1.9%. A lookup now checks about half as many entries, 11.5
instead of 24.0 on the IDE and terminal clip, because no entry points
into another tile.

Splitting the table didn't help compression much. The number of blocks
where the hash search finds a copy went up by only 0.1 to 0.7%. Repeated
content usually has many copies, so the first 64 entries already held a
valid one.

### Correctness

With one tile, the table and the output are the same as with the flat
table alone. With more than one tile the output changes, because each
tile's buckets now hold different entries. A tile's walk visits its
positions in the same relative order as the frame walk, so a tile's bucket
is the frame bucket without the entries from other tiles, then capped. It
can only gain valid candidates, never lose one.

We checked it like this:

- Every key frame was decoded with libaom and compared against the
  encoder's reconstruction in 22 configurations, covering 1 to 8 threads,
  tile layouts from 1x4 to 4x4, size changes and the good quality and all
  intra modes. There were no mismatches.
- On the benchmark clips the output got 0.004 to 0.032% smaller at two
  threads and 0.003 to 0.048% smaller at four.
- The output on the M3 Pro and on the Ryzen matched byte for byte.
- A new unit test, `IntraBCTilesTest`, encodes a key frame of repeated
  noise with several tile layouts and thread counts and checks that
  IntraBC saves more than half of the frame. With the flat table alone,
  the case with 4x2 tiles on 2 threads saved only 34%, because the first
  64 entries of every bucket belonged to the left tiles. So crowded
  buckets can matter, just not on these recordings.

### Memory

Each tile caps its own buckets at 256, so a frame can keep a few more
entries in total. In an earlier M3 Pro run on two clips, the entries grew
by 1 to 8%. The bucket arrays shrink at the same time because only 8x8
is built. At 2x2 tiles four tables take 1.57 MB of bucket arrays, against
2.36 MB for the single table before.

Modes that build more block sizes need more. By our calculation, not a
measurement, a good quality encode with 64x64 superblocks and 2x2 tiles
needs 7.9 MB of bucket arrays, and 31.5 MB with 16 tiles. With 128x128
superblocks one more block size is built, which makes it 9.4 MB and
37.7 MB.

### Speed

At four threads the per tile build takes another 22 to 27% off the key
frame on the M3 Pro (13 to 15 ms) and 28 to 32% on the Ryzen (31 to 36
ms). With two threads it helps less, 9 to 12% and 16 to 19%, because only
two workers share the build. It won every round on every clip, except one
round each on two clips on the Ryzen.

At one thread there is a single tile, so nothing changes: the output is
the same and the time moved by -1.0 to +1.1%, which is noise. The total
CPU time at two and four threads moved by -2.5 to +3.4%. The per clip
times are in the Results section below.

## Results

The chart shows all three versions at one, two and four threads, as the
mean over the five clips. Each version keeps its color.

{{< svgchart "charts/intrabc-threads.svg" "Wall time per key frame, mean of the five clips. The label is the change of the mean, both patches against main." >}}

At one thread the flat table does all of the work, because the per tile
build has nothing to split. With more threads the per tile build counts
more, and at four threads the two patches together cut the key frame time
about in half on both machines.

Per clip at four threads:

{{< svgchart "charts/intrabc-wall-4t-all.svg" "Wall time per key frame at four threads. The label is the change of both patches against main." >}}

Per clip at two threads, with 2x1 tiles:

{{< svgchart "charts/intrabc-wall-2t-all.svg" "Wall time per key frame at two threads. The label is the change of both patches against main." >}}

Both patches together won every round against main on every clip at one,
two and four threads on both machines, except one round at four threads
on the Ryzen.

## What IntraBC costs now

How much does IntraBC still cost after all this? We measured it in a
separate run on each machine: the same clips and settings, five rounds,
libaom main against main with our patches. Both machines produced the
same output hashes.

### Apple M3 Pro

Extra time per key frame with IntraBC on, compared with IntraBC off:

{{< svgchart "charts/intrabc-cost.svg" "Extra key frame time from IntraBC on libaom main and with our patches. The label is the patched extra time as a share of the key frame with IntraBC off." >}}

On main, turning IntraBC on made a four thread key frame 2 to 2.5 times
slower. With the patches it costs 10 to 19% more.

The difference between on and off understates the real cost. With
IntraBC on, the encoder skips some other work and writes a smaller file
(5.19 MB against 6.46 MB for the IDE and chat clip). So we also timed
each stage directly. The timers add up to 3% overhead and don't change
the output. Medians over the 100 key frames per clip from the five
rounds:

{{< svgchart "charts/intrabc-stages.svg" "IntraBC time per key frame by stage at one thread, with our patches." >}}

- At one thread IntraBC takes 18 to 21 ms, 14.5 to 16.1% of the key
  frame. The table build is 10.4 to 12.1% and the search 3.9 to 5.1%.
- At four threads the table build takes 4.0 to 4.7 ms of wall time, 10 to
  12% of the key frame.
- The fill is the largest single stage, then the search. About 40% of
  the search is the hash lookup itself.

In a sampling profile IntraBC is no longer the top of the list.
Coefficient coding (8.0 to 9.3% of samples) and the palette k-means (6.5
to 7.5%) come first, then the IntraBC count and fill (5.4 to 6.4%). All IntraBC code together
is 13.6 to 16.1% of samples.

#### What limits each stage

We read the M3's hardware counters with Instruments (`xcrun xctrace`) and
charged each sample to the stage on its call stack. Per position, IDE and
chat clip, one thread:

| Stage | Cycles | Instructions | IPC | L1D load misses | L1 TLB misses |
|---|---|---|---|---|---|
| 2x2 pass | 2.4 | 15.2 | 6.4 | 0.04 | 0.005 |
| CRC, each pass | 2.1 | 13.3 | 6.3 | 0.14 | 0.001 |
| Count | 1.6 | 8.2 | 5.0 | 0.19 | 0.000 |
| Fill | 4.9 | 12.2 | 2.3 to 2.5 | 0.75 | 1.30 |

The counts are measured. What follows is our reading of them.

- The CRC passes take about 2.1 cycles per position for two CRC
  instructions. That is consistent with one CRC per cycle. Apple doesn't
  document the CRC throughput, so we can't say for sure that this is the
  limit.
- The 2x2 pass and the count are compute bound, at an IPC of 5 to 6.4.
- The fill is the slow one, at an IPC of 2.3 to 2.5. The column walk
  lands on a new 16 KB page on every read, and the entry stores scatter,
  so each position costs 1.3 L1 TLB misses. Nearly all of them hit the
  second level TLB, and only 4% of the cycles wait on cache misses. So on
  the M3 the fill is most likely limited by address translation and
  scattered stores, not by memory bandwidth.
- Each hash lookup in the search takes 13 to 23 L1D load misses and 0.12
  to 0.24 microseconds. A lookup walks a bucket in a 10 to 15 MB array
  that is mostly cold.

### AMD Ryzen 9 5900X

{{< svgchart "charts/intrabc-cost-x86.svg" "Extra key frame time from IntraBC on libaom main and with our patches. The label is the patched extra time as a share of the key frame with IntraBC off." >}}

On main, turning IntraBC on made a four thread key frame 2.6 to 2.9 times
slower. With the patches it costs 16 to 23% more. Measured this way, the
Ryzen spends 23 to 30 ms of CPU per key frame on IntraBC at one thread,
against 8 to 12 ms on the M3. The stage timers below are the fairer
comparison, and they give about twice the M3's time.

{{< svgchart "charts/intrabc-stages-x86.svg" "IntraBC time per key frame by stage at one thread, with our patches." >}}

- At one thread IntraBC takes 35 to 42 ms, 17.0 to 18.0% of the key
  frame. The table build is 12.3 to 14.5% and the search 3.5 to 4.9%.
- At four threads the table build takes 10.7 to 13.0 ms of wall time, 14
  to 16% of the key frame. On the M3 it is 10 to 12%.
- The fill is again the largest stage, at 12.4 to 17.1 ms, 2.4 to 2.7
  times its time on the M3. The search comes next at 7.0 to 10.7 ms, and
  about a third of that is the hash lookup.

The profile here covers the whole process, including encoder setup,
frame reading and the kernel, so its shares don't compare directly with
the M3's. Coefficient cost is still first (7.1 to 9.2% of samples), but
the count and fill function is second at 5.5 to 6.3%. All IntraBC code together takes 10.4 to 12.5% of
samples at one thread.

#### What limits each stage

On Windows we sampled the counters with xperf and mapped each sample to a
source line, which splits the count from the fill inside one function.
xperf has no TLB or L3 events on this machine, so the table shows L2
misses instead. Per position, IDE and chat clip, one thread:

| Stage | Cycles | Instructions | IPC | L1D misses | L2 misses |
|---|---|---|---|---|---|
| 2x2 pass | 6.5 | 22.4 | 3.4 | 0.08 | 0.010 |
| CRC, each pass | 4.2 | 15.6 | 3.7 | 0.19 | 0.014 |
| Count | 3.8 | 11.4 | 3.0 | 0.27 | 0.011 |
| Fill | 13.4 | 15.5 | 1.2 | 1.91 | 1.05 |

Our reading:

- The 2x2 and CRC passes run at an IPC of 3.4 to 3.7 with almost no
  cache misses, so memory isn't their limit. They run more instructions
  per position than on the M3, 22 against 15 for the 2x2 pass. That
  compares MSVC on x64 with clang on arm64, so it says little about either
  compiler. The CRC passes take 4.2 cycles per position, against 2.1 on
  the M3.
- The count is cheap at 3.8 cycles per position.
- The fill misses L2 about once per position and runs at an IPC of 1.2,
  so it waits on L2 misses, probably served from the L3. Two likely
  sources: the entry stores, which scatter across a table of about 15 MB,
  and the 384 KB of count and start arrays, which compete with everything
  else for the 512 KB L2. We can't measure TLB misses here. With 4 KB
  pages, address translation probably costs more than on the M3, and the
  L2 miss count includes page walks, so some of those misses may be
  walks.
- 9 to 17% of the build's cycles are kernel time, most likely page
  faults on freshly allocated memory. The encoder allocates its two frame
  sized hash buffers on every key frame, and our harness also creates a
  new encoder per frame, so the table arrays are fresh too. A long running
  encoder avoids only the second part.
- Each hash lookup takes 0.15 to 0.25 microseconds, with 17 to 33 L1D
  misses and 5 to 9 branch mispredicts. It reads up to 64 entries of a
  cold bucket, and the compare branches depend on the data.
