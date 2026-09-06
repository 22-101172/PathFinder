// ============================================================
// PathFinder KG-Engine — Graph Reset Script
// Deletes all nodes and relationships.
// Constraints and indexes are preserved intentionally so that
// load.cypher can immediately MERGE without recreating them.
//
// Run this BEFORE load.cypher when rebuilding from updated CSVs.
// ============================================================

MATCH (n) DETACH DELETE n;
