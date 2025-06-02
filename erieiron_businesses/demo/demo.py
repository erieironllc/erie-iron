import sys


class Calculator:
    def __init__(self):
        pass

    def add(self, num1, num2):
        return num1 + num2


def execute():
    print("starting execution of demo calculator")
    calc = Calculator()
    result = calc.add(3, 4)
    print("result of adding 3 and 4:", result)
    print("execution complete")


if __name__ == "__main__":
    execute()
