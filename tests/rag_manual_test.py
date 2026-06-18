if __name__ == "__main__":
    from adapters.rag_adapter import RAGAdapter

    queries = ['What is the graduation requirements?'
        # "What is the attendance policy?",
    #     "What happens if I miss the final exam without an approved excuse?",
    #     "What is the rule for retaking a failed course?",
    #     "How many credit hours can I register if my CGPA is 2.5?",
    #     "What are the graduation requirements?",
    #     "What is the academic warning policy?",
    #     "What is the withdrawal rule?",
    #     "Can the handbook predict my GPA?",
    ]

    adapter = RAGAdapter()

    for q in queries:
        print("\n" + "=" * 80)
        print("QUERY:", q)
        result = adapter.execute(q)
        print("STATUS:", result.get("status"))
        print("ANSWER:", result.get("answer"))
        print("CITATIONS:", result.get("citations"))
        print("METADATA:", result.get("metadata"))