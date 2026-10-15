-- Migration 037: merge catalog entries that are the same recording
--
-- The catalog keys a song on its normalized title and artist, so the same
-- recording landed under several entries whenever a play spelled it
-- differently: [Clean]/[Explicit] and BPM tags, "(Bonus Track)" and other
-- release labels, a featured artist in the title on one play and in the
-- artist on another, punctuation exported as "_", curly vs straight
-- apostrophes, mangled accents (VOILĄ, Maītre, ROĖDS) and truncated artists
-- (Mar, Davina). These 83 groups were reviewed and approved one by one on
-- 2026-10-15; remixes, acoustic, stripped and live versions stay separate.
--
-- Each group names its canonical entry (true) and the entries folded into
-- it (false). Folded with them is any other entry sharing a listed entry's
-- normalized title and artist: the same song held under another owner.
-- Plays move to the canonical entry, it keeps the first genre, BPM and
-- release year any of them had, its play count and first/last played are
-- recomputed from its plays, and the folded entries are deleted. Play rows
-- themselves are untouched apart from catalog_id.
--
-- Keyed on production ids: a database without them changes nothing, and
-- once applied the folded ids are gone, so a re-run changes nothing.

CREATE TEMP TABLE catalog_merge (grp int, catalog_id uuid, canonical boolean) ON COMMIT DROP;

INSERT INTO catalog_merge (grp, catalog_id, canonical) VALUES
  -- 1. On The Rocks — Grieves
  (1, 'b6f87c35-b48c-42a6-a08b-6f4042cb90c1', true),
  (1, '070f3a64-1193-4bb3-92d8-a1524d9237b3', false),
  -- 2. enjoy — Janet Jackson
  (2, '7261254e-c5e4-4155-adc7-c6f912a014f1', true),
  (2, '916e97b9-68bd-4632-9c14-ab229171cefd', false),
  -- 3. G Train (Feat. Pusha T) — Thirdstory
  (3, '41698019-d453-4ad8-a537-c6eed8fe1ddc', true),
  (3, 'ff8c6cda-a2b0-4e72-8a41-bd40c7c945af', false),
  (3, 'a396ea5f-7933-4b4f-9c14-69b74f5029c5', false),
  -- 4. Drops (feat. Tom Bailey) — FKJ
  (4, 'a3616af4-9868-4906-8ec9-b110657be90d', true),
  (4, '1666021d-6c5e-4c56-a3be-ed25e8a3e6d4', false),
  -- 5. If You're Hearing This — Hook N Sling
  (5, '29575465-65f1-4cd7-8148-331df532aae8', true),
  (5, 'b4f81268-e35d-4361-a2dd-82243054140d', false),
  -- 6. Mm, Mm, Mm — Patti Labelle
  (6, '5bdab70e-dfca-40ba-8dcd-81826faf0066', true),
  (6, '2683ab35-3418-49f4-84b5-abbfaecb75cc', false),
  -- 7. Issa Bounce — Upsidedown
  (7, 'add89b92-3de1-4e5d-a211-2ecd8156dc6a', true),
  (7, 'dc601709-a52c-437f-8688-427ec54cd9f0', false),
  -- 8. Love On Top — Beyoncé
  (8, 'e77d7e47-10ec-495a-a28e-332e6d232054', true),
  (8, 'dfd2cb32-04c9-47fc-a268-cac45429e56e', false),
  (8, '6d985939-c162-4bfa-b1d7-5cf952ef580d', false),
  -- 9. All We Know — The Chainsmokers & Phoebe Ryan
  (9, 'f50674e1-6f9a-4b9a-86ae-68037495c957', true),
  (9, '06f7b592-3a54-4065-a30d-c47c58d011f3', false),
  -- 10. Can't Help But Wait — Trey Songz
  (10, '38f381d4-4043-4323-a8e4-e2f352021c76', true),
  (10, 'bd6c60a0-5b52-423b-a4b0-b1675a5cc17a', false),
  -- 11. Dynamite — Nause & Pretty Sister
  (11, 'ff0f974f-03b5-4c82-8546-f4ed9fa421ad', true),
  (11, 'ef6ddf67-d737-40aa-9768-bdca1cbdd761', false),
  -- 12. Another Love — Blush'ko
  (12, '24b7144a-9e9f-4983-87d2-1e531c6db7dd', true),
  (12, '897fb21b-017b-46a6-b033-3ca35b12ea49', false),
  -- 13. Diddy Bop — Jacob Banks & Louis The Child
  (13, 'eea8c796-43d1-4248-a86a-073526457a72', true),
  (13, '458b3af0-ff04-4359-aae5-ac66f85afbb4', false),
  -- 14. California Girls — NoMBe and Sonny Alven
  (14, 'b3a9895b-c093-4106-abc3-aa8fc90736e6', true),
  (14, '87b4ab8e-b6d3-4642-8cf6-00c04dd7cb6e', false),
  -- 15. So Simple (Feat. G-Eazy) — Marc E. Bassy
  (15, 'fa583537-823c-44c6-afdc-8e134d7b7015', true),
  (15, '60dbfe23-d943-4a10-823c-787844fcf7ef', false),
  -- 16. Buttons — Pussycat Dolls
  (16, '31c05e58-311d-4e19-9eb6-d1fa59b3ee11', true),
  (16, 'c3c7b45c-7f19-4b1d-b2ec-7355c19a2c9c', false),
  -- 17. Smile Like That — Terror Jr
  (17, '9d50c105-561b-4b7b-af11-35a79cbb1e69', true),
  (17, 'ba52a3ed-1fac-4961-b83b-0359b0058b12', false),
  -- 18. Torn — James TW
  (18, 'b9929493-05c5-43ca-8169-1777e7c4f635', true),
  (18, '5a4335fc-a252-45fb-83d9-b03b61dbc6ba', false),
  -- 19. 12345SEX — Upsahl
  (19, '18d96de0-9ac0-4378-bc5b-0aab7f64e7e6', true),
  (19, '91a26743-c6ed-448e-8f76-3a38de3165cc', false),
  -- 20. California King — D.B. Ricapito
  (20, '0e04c4a7-e96b-476a-8c07-d10a0bcbb30e', true),
  (20, '0a478052-b1f2-4116-b64c-e8b226a49d00', false),
  -- 21. Candy [Clean] — Kah-lo
  (21, 'ee10c70a-197c-4241-adad-ab0586e1b2c1', true),
  (21, 'a5be777f-1939-4f70-a699-e9623cb712aa', false),
  -- 22. Springsteen — Eric Church
  (22, 'dd40bdb9-488f-4f01-8215-79d2b31b38ee', true),
  (22, '0a18f850-0fb6-45ff-9d2f-8b9cb8e25ce9', false),
  -- 23. Figure You Out — VOILÀ
  (23, 'f4039484-8e92-4df2-a3d5-6016a13ccac8', true),
  (23, 'fbbf8d7a-cf07-4032-8b8a-eae0c5f59fda', false),
  -- 24. Just Wanna Dance — Spencer Ludwig
  (24, '6445c553-4273-4489-b533-97f382819a8f', true),
  (24, 'ba79e1d5-a909-47c4-ab7f-78c0ce13d59f', false),
  -- 25. One, Two Step — Ciara & Missy Elliott
  (25, '735d4240-6ccd-4701-959e-96fa99ad942b', true),
  (25, '3318c011-da63-4b42-992e-14bffa9ea7a0', false),
  -- 26. 9 to 5 — Kelly Clarkson and Dolly Parton
  (26, 'f04e8064-bc73-4a72-9585-0dededfb7ae3', true),
  (26, 'a35fccca-2129-4f21-8c41-19f3fe99ccb1', false),
  -- 27. Can't Hide (Feat. Ashe) — Whethan
  (27, '23ab3382-60e5-41c1-abb9-32dd6fb2cc36', true),
  (27, '590729c8-1b0b-4797-a103-9d3c7c537092', false),
  -- 28. AFU [Explicit] — Cal
  (28, '15e013a4-e4fa-4607-8070-eaa896e58021', true),
  (28, '1ec79ec8-f29e-4544-a2b0-3b017fd78240', false),
  -- 29. Walk That Walk (With Nic Hanson) — Bakermat
  (29, 'dd3ff83f-1879-4779-936d-8bc5aea0dda0', true),
  (29, '5fa267dd-d032-42ac-b836-aaa9c93959e0', false),
  -- 30. 519 — Matt Wertz
  (30, 'fd8598b6-fcfb-43da-8353-d50c1e5d2f16', true),
  (30, 'f9f6bc3c-e4cc-465b-a2f3-d4c83dca62c0', false),
  -- 31. Back To Me — Marian Hill & Lauren Jauregui
  (31, '5550ee15-4bc2-4d6d-9a9c-e97d98f99c38', true),
  (31, '8e0cb9df-1d74-46c8-9cb8-1fe016a45691', false),
  -- 32. Can't Figure It Out — Slaptop
  (32, '2d033d95-4072-4716-ba88-87a191d87265', true),
  (32, '458a8bae-8bfc-4fe5-a6af-bfc29acc2482', false),
  -- 33. I Don't Like It, I Love It — Flo Rida & Robin Thicke & Verdine White
  (33, 'd01431d8-639d-4238-9fa8-32490e0e5364', true),
  (33, '87c2a535-8faa-4f2a-b19f-fd2988acb9bc', false),
  -- 34. It Runs Through Me — Tom Misch, De La Soul
  (34, '33dbf0fb-32b7-4254-8bc0-0deff416a08e', true),
  (34, 'd60c39b2-cf4a-4d58-afa9-989b67575881', false),
  (34, '392b61f9-f206-4088-a3c6-236a5f431cea', false),
  -- 35. Reste (Feat. Sting) — Maître Gims
  (35, 'b7290a85-b543-4af0-a217-1fafe1e1a6fa', true),
  (35, 'cae90a39-f382-4c11-9ae2-c8576ff3f486', false),
  -- 36. The Blues 'is' — Bryan Lee And The Jump Street
  (36, 'b2f427c1-306e-494c-879f-dcd96b1cea2a', true),
  (36, 'abdc4bbb-b10a-4f55-abb2-8ca53bc80e83', false),
  -- 37. Willy Wonka (feat. Paulina & Jafé) [Explicit] — Russ
  (37, '66b955ce-c614-4313-9988-997349fc2b05', true),
  (37, '6ee6ee84-ea56-4a18-be88-4bc47388cb12', false),
  -- 38. edamame — bbno$ featuring Rich Brian
  (38, '58690d8a-f882-4b1a-9b0d-9a7149aca382', true),
  (38, '99de155e-32f4-4e6b-9af3-0d6e986ae721', false),
  -- 39. Pretty Please — Dua Lipa
  (39, '58cee2a6-c8c1-4a78-9f9d-fedc2b9def05', true),
  (39, 'ad80f486-afe2-4bd0-b7ed-e4b047781e80', false),
  -- 40. 2 AM — Adrian Marcel & Sage The Gemini & Problem
  (40, '0bff0aa7-cc80-424f-815b-c463bf68c21c', true),
  (40, 'd6912609-4896-45c3-bf00-d42d7df90492', false),
  -- 41. 6 foot deep — Emeline
  (41, '0b751567-8041-4b0b-a1dd-745a771189ee', true),
  (41, '6cfe598b-9db5-423d-bbc2-e53345d059f5', false),
  -- 42. Burning Down — Alex Warren, Joe Jonas
  (42, '61b89ad0-74a7-46f2-9252-30d18d9fd387', true),
  (42, 'a8555e27-5e82-489a-9ff9-cea628fd1eb1', false),
  -- 43. do u really? — Lyn Lapid & Ruth B.
  (43, 'aaa20455-90fe-49c2-9c1b-7f6ec384e54a', true),
  (43, '6fdbd405-c186-4277-a34f-aa4aa5bcff79', false),
  -- 44. Feelin' — ROËDS
  (44, '62b87537-bfba-47ea-941a-cfebe03bbef9', true),
  (44, '117d10b1-ad89-42f6-9ee0-801287a2e624', false),
  -- 45. Suga Suga — Baby Bash
  (45, 'e4cd852a-2050-4341-8c8d-358ef2e03104', true),
  (45, 'ee9d55ae-1ccd-4aef-9a87-6aa6499caed0', false),
  -- 46. That's Not How This Works (Feat. Dan & Shay) — Charlie Puth
  (46, '8ab26ae6-4257-43d0-ad51-4a82107d515f', true),
  (46, '8b39de1a-40a4-4867-b904-58761dbe0972', false),
  -- 47. Twerk It Like Miley — Brandon Beal & Christopher
  (47, 'd74a0ecd-8b6b-4f64-a5f2-7096e6e24dac', true),
  (47, 'c88e0f98-5d99-4e2f-b585-402d4c9ac954', false),
  -- 48. Big (feat. Gunna) — Rita Ora,David Guetta,Imanbek,Gunna
  (48, 'fdf80761-0024-4d98-9efc-ab4a3cdef7d0', true),
  (48, '0dcff3e9-0ec4-41a2-a046-685c9e4c8afb', false),
  (48, '1706aabd-7292-43f1-859f-92b1bbf59efa', false),
  -- 49. Don't Let Go — Strong Asian Mothers
  (49, 'af6922ce-ad46-40b1-ba35-fcc3729cdfd8', true),
  (49, '9d1867b6-c26e-4402-8672-7482a58c0381', false),
  -- 50. En Secret (Feat. Vitaa) — Maître Gims
  (50, '9a88ade5-88f6-440d-b977-12ecc9a901a6', true),
  (50, 'b34b14e6-b608-498d-a0ca-9c4321d358be', false),
  -- 51. Like 1999 — Valley
  (51, '7727680f-3a62-4a6d-9d7b-240e0e784733', true),
  (51, '668b35e9-74ab-4d90-88fc-6643cfe27676', false),
  -- 52. Merry Christmas, Baby (Feat. Cee Lo Green) — Rod Stewart
  (52, '452ae203-1874-4ace-af7d-e89c8be29814', true),
  (52, 'f56d938e-f8d7-4cc1-9163-0c19e927b560', false),
  -- 53. Take Me Back — Brick & Lace
  (53, 'b55a873c-59ad-467c-9a5c-f806c82052d8', true),
  (53, 'c590c243-1c78-457b-84d1-b3707538f066', false),
  -- 54. Cupid Shuffle — Cupid
  (54, 'cab19bb9-1fe2-4226-a011-eafc100c9a6f', true),
  (54, '42afadc8-caf6-495c-b8c6-abee5e5c7dd1', false),
  -- 55. lonely — Lauv feat. Anne-Marie
  (55, '8c74fbc7-8d92-47ad-84b3-65f2750ca1e2', true),
  (55, '9b2ecbd1-8647-4137-8d3c-15b19df4b42f', false),
  -- 56. Quantum Physics — Ruby Waters
  (56, '3e8a8958-7338-4b6a-8f21-3e8335758575', true),
  (56, '326518a5-69cd-42fc-a3b7-84d6da737995', false),
  -- 57. Something Just Like This — The Chainsmokers & Coldplay
  (57, 'fe2f8f12-fb91-4fe4-bcd1-aec43f4915a9', true),
  (57, '6d83b11c-2581-4880-a72d-ecac05e5f5ac', false),
  -- 58. Don't Play That Song — Aretha Franklin
  (58, 'aa58b028-07df-4a50-ae52-0b4f4c75071a', true),
  (58, 'd6095659-e53b-4653-a629-00a5c005c4ab', false),
  -- 59. I Ain't Jivin', I'm Jammin' — Leon Huff
  (59, '82939d62-e9d8-4f1d-84bd-c712822bc5a2', true),
  (59, '65b9f82e-0056-4315-8b1f-f2e3b95ebb1c', false),
  -- 60. Sexyback (CLEAN) — ilkan Gunuc & Clara Stegall
  (60, 'e4371a1a-269a-432a-b958-9879cbe39899', true),
  (60, '76536c1b-dc9a-455a-9b64-a03d4d691200', false),
  -- 61. Say So (Vasco Remix) — Terror Jr
  (61, '406b299a-8370-4f1f-a087-f62d04b633e6', true),
  (61, '85d219b4-011c-4b63-a9ee-7882a1e4cdc0', false),
  -- 62. Vampire (Aobeats Remix) — Lazyboy Empire
  (62, '1d57bed1-210f-44d0-b603-294da4f0a9ca', true),
  (62, '0283de2a-5be9-4adb-97f0-759682b2376e', false),
  -- 63. It Wasn't Me (Le Boeuf Remix) — Shaggy
  (63, '5bc8fcca-0dfb-4282-89d4-c7c56e242b82', true),
  (63, '0efd9b81-c218-4694-b8d4-0b085fe2af5d', false),
  -- 64. Love Yourself (Justin Bieber Cover) — William Singe
  (64, '2e9522e0-0b47-4057-933f-33d1c4cb46ab', true),
  (64, 'bcbdcc9a-be99-417a-843f-0f610cf0b54e', false),
  -- 65. Sunflower (Spider-Man: Into The Spider-Verse) (Feat. Swae Lee) — Post Malone
  (65, '5c2cf4e9-0f2c-4632-a3d1-68fe3b00c10b', true),
  (65, '8461a108-56cd-42d2-9ddb-d275f2950bd7', false),
  -- 66. Ain't Leavin Without You — Jaheim
  (66, '401da403-3bf7-4b58-96d4-ff5f98b49ea3', true),
  (66, 'cfe963f1-43a5-4f70-a187-a5f2b67955a9', false),
  -- 67. Rascal (Superstar) — Tinashe
  (67, '2ee5e9bb-7861-46f5-9f6b-934f8a44663e', true),
  (67, '1b2d8226-ceea-4154-8d92-8bd605316111', false),
  -- 68. Can I Get A Witness — SonReal
  (68, '04fb755e-54e5-4eb8-b3f7-f0240d93a0a6', true),
  (68, 'b1708941-d776-4c17-bb44-d5169e424a50', false),
  -- 69. 6's To 9's (Feat. Rationale) — Big Wild
  (69, '090a0ccf-4b73-4111-afba-782a0ebb6f6f', true),
  (69, 'd957e34b-ebc5-44a7-aab5-ba698f9c24a2', false),
  -- 70. Eastside (Acoustic) — benny blanco & Halsey & Khalid
  (70, 'a00de4f1-e688-4977-a8ff-e70b1405c6d0', true),
  (70, 'eda4b6df-0888-4dae-8ff7-bcecbd4149f6', false),
  -- 71. DJ Don't — Gerald Levert & Jaheim
  (71, '79b6de8d-de06-450f-b844-9f46b72a9249', true),
  (71, '726cb544-23c7-454b-92a7-15cd458013b9', false),
  -- 72. Raindrops — Katja Krasavice x Leony
  (72, 'a5739dc0-53fb-478e-8aef-dacd50d8c9c1', true),
  (72, 'a8142f18-a880-46fa-9d6a-0b13d8ddfd8f', false),
  -- 73. Ain't Nobody — Jasmine Thompson
  (73, '2dbc7ce9-0efc-40eb-9bc9-1b5d907c1ded', true),
  (73, '1ca99789-fa60-49bd-b243-2b92d023d4be', false),
  -- 74. Ain't Nobody (Loves Me Better) (Acoustic) — Jasmine Thompson
  (74, '4526080d-bdd9-4c4c-ba55-6fe9e33433a6', true),
  (74, 'e913c8b8-77ae-4fff-b499-6cc430a11fb5', false),
  -- 75. Capricorn (Live Acoustic) — Elderbrook
  (75, 'f5d507f9-18b7-443b-9cc5-ba4fd049f5c2', true),
  (75, '31c44803-436a-458f-bfbe-3249f7705ec1', false),
  -- 76. Cruel - Piano Acoustic — Oli Fox
  (76, '5fd293b5-9e79-49e0-b929-ea6865976b19', true),
  (76, 'f148db30-3bf0-42ea-8016-cfbb9abfd406', false),
  -- 77. U Remind Me (Recorded at Spotify Studios NYC) — James TW
  (77, 'f5eaf4c2-f732-4c2a-a17d-7847dfe1ed77', true),
  (77, 'c2496dfb-34fa-48d2-8170-14b30ba4c493', false),
  -- 78. Talk Slow — Cody Lovaas
  (78, 'c6ce2a5d-e876-40e0-a0fc-1317476a03fc', true),
  (78, '083da4ad-d13b-467a-bb59-8cd77021d8f5', false),
  -- 79. 911 — Teddy Swims
  (79, 'f13551ae-91ce-4df6-8a0a-877cc4e2adca', true),
  (79, '2a798c9f-b7a4-4add-b321-78eb6f36bc83', false),
  -- 80. A Simple Go — Mar Omin
  (80, '50c3394c-b470-44c1-9e68-d089f0115475', true),
  (80, '8c7647cf-6e9c-45ef-8894-70f59a96bba7', false),
  -- 81. Now You've Been Seduced — Davina Joy
  (81, '96adc442-15fc-4cb7-b2e1-f9068bbaf2fd', true),
  (81, '9a5fe335-9da4-4bde-91ab-08c12721704a', false),
  -- 82. king (acoustic) — slenderbodies
  (82, 'ac55f86e-3f41-4962-9cb1-2b4086347509', true),
  (82, '7cb13069-6e33-4f8e-bc97-4225aa6355a0', false),
  -- 83. Gonna Make You Sweat (Everybody Dance Now) — C&C Music Factory
  (83, 'ce0f85e1-b192-4f04-a442-9255136415ee', true),
  (83, 'dde1d753-6814-4c36-8fb4-95f9779288ad', false);

-- The canonical entry of each group that exists in this database.
CREATE TEMP TABLE catalog_merge_canon ON COMMIT DROP AS
SELECT m.grp, m.catalog_id AS canon_id
FROM catalog_merge m
JOIN track_catalog c ON c.id = m.catalog_id
WHERE m.canonical;

-- Every entry folding into a canonical one: each listed entry plus any
-- entry with the same normalized title and artist under another owner.
CREATE TEMP TABLE catalog_merge_moves ON COMMIT DROP AS
SELECT DISTINCT k.canon_id, c.id AS old_id
FROM catalog_merge m
JOIN catalog_merge_canon k ON k.grp = m.grp
JOIN track_catalog listed ON listed.id = m.catalog_id
JOIN track_catalog c
  ON c.title_normalized = listed.title_normalized
 AND c.artist_normalized = listed.artist_normalized
WHERE c.id <> k.canon_id;

-- Fill the canonical entry's empty metadata from the entries folding in,
-- most played first.
UPDATE track_catalog c
SET genre        = COALESCE(c.genre, f.genre),
    bpm          = COALESCE(c.bpm, f.bpm),
    release_year = COALESCE(c.release_year, f.release_year)
FROM (
  SELECT mv.canon_id,
         (array_agg(o.genre ORDER BY o.play_count DESC) FILTER (WHERE o.genre IS NOT NULL))[1] AS genre,
         (array_agg(o.bpm ORDER BY o.play_count DESC) FILTER (WHERE o.bpm IS NOT NULL))[1] AS bpm,
         (array_agg(o.release_year ORDER BY o.play_count DESC) FILTER (WHERE o.release_year IS NOT NULL))[1] AS release_year
  FROM catalog_merge_moves mv
  JOIN track_catalog o ON o.id = mv.old_id
  GROUP BY mv.canon_id
) f
WHERE c.id = f.canon_id;

UPDATE tracks t
SET catalog_id = mv.canon_id
FROM catalog_merge_moves mv
WHERE t.catalog_id = mv.old_id;

DELETE FROM track_catalog
WHERE id IN (SELECT old_id FROM catalog_merge_moves);

-- Play count and first/last played, recomputed from the plays now linked.
UPDATE track_catalog c
SET play_count   = p.plays,
    first_played = p.first_played,
    last_played  = p.last_played
FROM (
  SELECT t.catalog_id, count(*) AS plays,
         min(s.set_date) AS first_played, max(s.set_date) AS last_played
  FROM tracks t
  JOIN sets s ON s.id = t.set_id
  WHERE t.catalog_id IN (SELECT canon_id FROM catalog_merge_canon)
  GROUP BY t.catalog_id
) p
WHERE c.id = p.catalog_id;
