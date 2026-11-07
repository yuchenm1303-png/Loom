import heapq
import itertools


class Queue:
    def __init__(self):
        self.items, self.count = [], itertools.count()

    def push(self, item, priority):
        heapq.heappush(self.items, (-priority, next(self.count), item))

    def pop(self):
        return heapq.heappop(self.items)[2]

    def peek(self):
        return self.items[0][2]

    def __len__(self):
        return len(self.items)
