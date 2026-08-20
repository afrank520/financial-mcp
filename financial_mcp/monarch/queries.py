"""Monarch GraphQL query strings.

Copied verbatim from the monarchmoney library (github.com/hammem/monarchmoney,
v0.1.15, MIT License) so we own our copy of the Monarch contract without a
dependency on that (now dead-domain) library. Only the read queries this plan
needs are included; Plan 3 will add more.
"""

GET_ACCOUNTS = """
  query GetAccounts {
    accounts {
      ...AccountFields
      __typename
    }
    householdPreferences {
      id
      accountGroupOrder
      __typename
    }
  }

  fragment AccountFields on Account {
    id
    displayName
    syncDisabled
    deactivatedAt
    isHidden
    isAsset
    mask
    createdAt
    updatedAt
    displayLastUpdatedAt
    currentBalance
    displayBalance
    includeInNetWorth
    hideFromList
    hideTransactionsFromReports
    includeBalanceInNetWorth
    includeInGoalBalance
    dataProvider
    dataProviderAccountId
    isManual
    transactionsCount
    holdingsCount
    manualInvestmentsTrackingMethod
    order
    logoUrl
    type {
      name
      display
      __typename
    }
    subtype {
      name
      display
      __typename
    }
    credential {
      id
      updateRequired
      disconnectedFromDataProviderAt
      dataProvider
      institution {
        id
        plaidInstitutionId
        name
        status
        __typename
      }
      __typename
    }
    institution {
      id
      name
      primaryColor
      url
      __typename
    }
    __typename
  }
"""

GET_TRANSACTIONS = """
  query GetTransactionsList($offset: Int, $limit: Int, $filters: TransactionFilterInput, $orderBy: TransactionOrdering) {
    allTransactions(filters: $filters) {
      totalCount
      results(offset: $offset, limit: $limit, orderBy: $orderBy) {
        id
        ...TransactionOverviewFields
        __typename
      }
      __typename
    }
    transactionRules {
      id
      __typename
    }
  }

  fragment TransactionOverviewFields on Transaction {
    id
    amount
    pending
    date
    hideFromReports
    plaidName
    notes
    isRecurring
    reviewStatus
    needsReview
    attachments {
      id
      extension
      filename
      originalAssetUrl
      publicId
      sizeBytes
      __typename
    }
    isSplitTransaction
    createdAt
    updatedAt
    category {
      id
      name
      __typename
    }
    merchant {
      name
      id
      transactionsCount
      __typename
    }
    account {
      id
      displayName
      __typename
    }
    tags {
      id
      name
      color
      order
      __typename
    }
    __typename
  }
"""

GET_ACCOUNTS_MIN = """
  query GetAccountsMin {
    accounts {
      id
      displayName
      includeInNetWorth
      type {
        name
        __typename
      }
      __typename
    }
  }
"""

GET_ACCOUNT_SNAPSHOTS = """
  query SnapshotsForAccount($id: UUID!) {
    snapshots: snapshotsForAccount(accountId: $id) {
      date
      signedBalance
      __typename
    }
  }
"""

GET_CASHFLOW_SUMMARY = """
  query Web_GetCashFlowPage($filters: TransactionFilterInput) {
    summary: aggregates(filters: $filters, fillEmptyValues: true) {
      summary {
        sumIncome
        sumExpense
        savings
        savingsRate
        __typename
      }
      __typename
    }
  }
"""

GET_AGGREGATE_SNAPSHOTS = """
  query GetAggregateSnapshots($filters: AggregateSnapshotFilters) {
    aggregateSnapshots(filters: $filters) {
      date
      balance
      __typename
    }
  }
"""

GET_BUDGETS = """
  query GetJointPlanningData($startDate: Date!, $endDate: Date!) {
    budgetData(startMonth: $startDate, endMonth: $endDate) {
      monthlyAmountsByCategory {
        category { id __typename }
        monthlyAmounts {
          month
          plannedCashFlowAmount
          actualAmount
          remainingAmount
          __typename
        }
        __typename
      }
      __typename
    }
  }
"""

GET_CATEGORIES = """
  query GetCategories {
    categories {
      id
      name
      group { id name type __typename }
      __typename
    }
  }
"""

GET_HOLDINGS = """
  query Web_GetHoldings($input: PortfolioInput) {
    portfolio(input: $input) {
      aggregateHoldings {
        edges {
          node {
            id
            quantity
            basis
            totalValue
            securityPriceChangeDollars
            securityPriceChangePercent
            security {
              id
              name
              type
              ticker
              typeDisplay
              currentPrice
              closingPrice
              __typename
            }
            __typename
          }
          __typename
        }
        __typename
      }
      __typename
    }
  }
"""

GET_RECURRING = """
  query Web_GetUpcomingRecurringTransactionItems($startDate: Date!, $endDate: Date!, $filters: RecurringTransactionFilter) {
    recurringTransactionItems(startDate: $startDate, endDate: $endDate, filters: $filters) {
      stream {
        id
        frequency
        amount
        isApproximate
        merchant { id name __typename }
        __typename
      }
      date
      isPast
      transactionId
      amount
      amountDiff
      category { id name __typename }
      account { id displayName __typename }
      __typename
    }
  }
"""

UPDATE_TRANSACTION_CATEGORY = """
mutation Web_TransactionDrawerUpdateTransaction($input: UpdateTransactionMutationInput!) {
  updateTransaction(input: $input) {
    transaction {
      id
      category { id __typename }
      __typename
    }
    __typename
  }
}
"""

CREATE_CATEGORY = """
mutation CreateCategory($input: CreateCategoryInput!) {
  createCategory(input: $input) {
    category { id name __typename }
    __typename
  }
}
"""

UPDATE_CATEGORY = """
mutation UpdateCategory($input: UpdateCategoryInput!) {
  updateCategory(input: $input) {
    category { id name __typename }
    __typename
  }
}
"""
